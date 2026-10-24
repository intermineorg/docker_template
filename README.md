## General commands

`make up` to bring up services (postgres, solr, tomcat)

`make shell` to start a shell in container with:
- environment needed to build a mine
- *data/workdir*, *data/maven.m2* and *data/properties.intermine* mounted into its home directory
- network access to services using hostnames and ports:
    - tomcat:8080
    - solr:8983
    - postgres:5432

`make build` before first use, or whenever you change the docker sources and
then run `make up` again to restart any changed services

`make down` to bring down and clean up services (only data in volumes will be
left)

To clear the data and start from scratch, delete `data` yourself and recreate
volumes with `make volumes`

## Other commands

`make logs` to see latest logs

`make restart` to restart all containers; prefer the per-service targets in
"Restarting individual containers" below

`make ps` to see the state and health of every container

To temporarily stop and later resume the containers without recreating them, use
`make stop` and `make start`

## Restarting individual containers

Every service can be restarted on its own except Postgres, which must be
followed by Tomcat. The reasons, from the actual runtime coupling:

| target               | restarts            | why it is safe / what to expect |
|----------------------|---------------------|---------------------------------|
| `make restart-nginx` | nginx               | Nothing inside the stack talks to nginx. The public site is unreachable for about a second. |
| `make restart-bluegenes` | bluegenes       | Stateless. nginx resolves `bluegenes` through Docker DNS at request time, so it picks the new container up by itself. |
| `make restart-tomcat` | tomcat             | nginx re-resolves `tomcat` as above; BlueGenes and the browser call the web service per request. The InterMine webapp needs several minutes to come back (`make ps` shows `starting`, then `healthy`). Sessions in the JSP webapp are lost; BlueGenes logins survive (API tokens live in the database). Postgres and Solr are untouched. |
| `make restart-solr`  | solr                | The webapp talks to Solr over stateless HTTP (SolrJ `HttpSolrClient`) by name, with Java's 30 s DNS cache, so no Tomcat restart is needed. Keyword search and autocomplete fail while Solr is down and work again once it is `healthy`. Nothing else uses Solr. |
| `make restart-postgres` | postgres, then tomcat | The webapp's read pool (HikariCP) re-validates connections and recovers by itself, but the userprofile *writer* keeps one long-lived connection and does not re-check it unless `osw.userprofile-production.robustConnection=true` is set in the mine properties. Without a Tomcat restart, logins, list saving and tagging would fail with "connection has been closed". The target waits for Postgres to be healthy before restarting Tomcat. |

`make wait-healthy-<service>` blocks until that container is healthy (15 minute
cap), which is handy in scripts: `make restart-solr && make wait-healthy-solr`.

Two things no restart target does:

- **Config or image changes.** `docker-compose restart` restarts the existing
  container as it is. After `make build`, or after editing `.env`,
  `docker-compose.yml` or anything under `nginx/`, `solr/`, `tomcat/`, run
  `make up` (recreates only the services whose config changed) or
  `docker-compose up -d <service>` for one. Recreating tomcat or bluegenes is
  fine for nginx because of the runtime DNS resolution; before this change a
  recreated tomcat with a new IP left nginx returning 502 until it too was
  restarted, and nginx refused to start at all while tomcat or bluegenes did
  not exist.
- **The webapp itself.** A new `webapp.baseurl` or a rebuilt webapp needs
  `./gradlew cargoRedeployRemote` from `make shell`, not a Tomcat restart.

Combinations that genuinely need more than one container: Postgres (always
with Tomcat, see above); a change of `MINE`, `INSTANCE` or `PUBLIC_URL` in
`.env` (`make up`, which recreates nginx, bluegenes and the builder, followed
by a webapp redeploy); and a Docker network recreation (`make down` /
`make up`), which brings everything back anyway.

## Solr: why it goes unhealthy, and what changed

Things to look at on an unhealthy Solr container. `make diagnose-solr` runs
all of the following with the container name taken from `.env`
(`${MINE}${INSTANCE}_solr`); to run them by hand, set `MINE` first
(`MINE=flymine` or `MINE=humanmine`):

```
docker inspect -f '{{.RestartCount}} restarts, {{.State.Health.Status}}, OOMKilled={{.State.OOMKilled}}, exit={{.State.ExitCode}}' ${MINE}_solr
docker inspect -f '{{range .State.Health.Log}}{{.End}} {{.ExitCode}} {{.Output}}{{end}}' ${MINE}_solr | tail
docker logs --since 24h ${MINE}_solr 2>&1 | grep -E -i 'OutOfMemoryError|oom_solr|Running OOM|too many open files'
docker exec ${MINE}_solr wget -qO - 'http://localhost:8983/solr/admin/info/system?wt=json' | grep -o -E '"-Xm[sx][^"]*"'
docker stats --no-stream
```

The most likely cause, and the one the template now fixes: **Solr was running
on the JVM default heap of 512 MB.** The original template set
`MEM_OPTS="-Xmx8g -Xms1g"` and built that into `JAVA_OPTS`, but Solr's start
script (`bin/solr`) never reads `JAVA_OPTS` or `MEM_OPTS`; only `SOLR_HEAP`
or `SOLR_JAVA_MEM` set the heap. Starting the image with the template's
variables and `solr start -f -V` shows `-Xms512m -Xmx512m`. (The quotes in
the compose value would also have been passed literally, and `-XX:+UseParallelGC`
would have conflicted with Solr's G1 settings had it been applied.)

A FlyMine-sized search index with faceting does not fit in 512 MB. What that
looks like in practice: continuous garbage collection makes `admin/ping` time
out, so the healthcheck fails and the container shows `unhealthy` while the
JVM is still alive; or Solr throws `OutOfMemoryError`, upon which its own
`oom_solr.sh` handler kills the process, the container exits, Docker's
`restart: unless-stopped` starts it again, and it is `unhealthy` while the
index is reopened. Note that Docker never restarts a container just for being
unhealthy; only an exit triggers the restart policy.

Changes:

- `docker-compose.yml`: `SOLR_JAVA_MEM=-Xms1g -Xmx8g` (the values the original
  intended), and the dead `MEM_OPTS`/`JAVA_OPTS` lines removed from
  `solr/solr.Dockerfile`. This must fit in host RAM next to Tomcat (4 GB) and
  Postgres (1 GB shared buffers plus up to 1 GB `work_mem` per sort), and
  next to a running build (up to 32 GB). If the host is short of memory the
  kernel OOM killer takes the JVM out instead (`OOMKilled=true`, exit 137 in
  the first command above).
- `docker-compose.yml`: a healthcheck `start_period` of 3 minutes for Solr
  (15 for Tomcat) with 5 retries, so reopening a large core after a restart
  no longer counts as failure. `version: "3.4"` is required for
  `start_period`.
- Tomcat's healthcheck now works at all: the image has no `wget`, so the old
  one failed on every run. It uses bash's `/dev/tcp` instead.

If Solr still ends up unhealthy after this, the logs will say why. Solr 8.4.1
is from early 2020; moving to the last 8.x release (8.11) is a low-risk
upgrade InterMine supports. To restart it automatically when it goes
unhealthy, a host cron entry per mine is enough (`crontab -e`; cron has no
`.env`, so the names are spelled out). Each line checks the health status
every five minutes and runs `make restart-solr` in that mine's directory only
when it reads `unhealthy`:

```
PATH=/usr/local/bin:/usr/bin:/bin
*/5 * * * * cd $HOME/intermine/docker/flymine   && docker inspect -f '{{.State.Health.Status}}' flymine_solr   | grep -q unhealthy && make restart-solr
*/5 * * * * cd $HOME/intermine/docker/humanmine && docker inspect -f '{{.State.Health.Status}}' humanmine_solr | grep -q unhealthy && make restart-solr
```

## Public URL and the URL embedded in generated code

`.env` has a fourth setting, `PUBLIC_URL`, the public origin of the site
(scheme + host, optional `:port`, **no path, no trailing slash**), e.g.
`https://www.flymine.org` or `https://www.humanmine.org`. It feeds two things:

- **BlueGenes**: `BLUEGENES_DEFAULT_SERVICE_ROOT=${PUBLIC_URL}/${MINE}` is the
  service root the browser talks to. `BLUEGENES_BACKEND_SERVICE_ROOT` stays
  `http://tomcat:8080/${MINE}` because the BlueGenes *server* lives inside the
  Docker network.
- **InterMine `webapp.baseurl`** in `data/properties.intermine/${MINE}.properties`.
  Whenever this property is set, InterMine's `URLGenerator` uses it verbatim
  (plus `/${webapp.path}`) for every URL it writes out: the `/service` URL in
  code produced by the results page "Generate code" dialog
  (`/service/query/code`), permalinks, OAuth2 redirects, list-sharing e-mails.
  It must therefore be the public URL. It is **not** the same thing as
  `webapp.deploy.url`, which must stay `http://tomcat:8080` (that is the Tomcat
  manager Gradle deploys the WAR to).

The builder container sets `webapp.baseurl` from `PUBLIC_URL` every time it
starts (`make shell`, `make baseurl`), via `intermine_builder/entrypoint.sh`
and `intermine_builder/set-webapp-baseurl.sh`. Because the mine properties are
copied into the WAR at build time, the webapp has to be redeployed after the
value changes:

```
make baseurl                 # or just `make shell`; both print what they changed
make shell
  cd workdir/<your mine>     # wherever the mine's gradle project lives
  ./gradlew cargoRedeployRemote    # rebuilds the webapp with the new properties
```

Tomcat (`tomcat/configs/server.xml`) now has a `RemoteIpValve` that trusts the
`X-Forwarded-For/Proto/Port` headers nginx already sends, so Tomcat sees the
request as `https`, port 443, from the real client IP. This keeps URL generation
correct in the fallback case too (blank `webapp.baseurl`), makes redirects and
secure cookies right, and puts real client addresses in the access log.

To check the live site (does what the "Generate code" dialog does and prints the
embedded service URL):

```
make check-baseurl
```

## nginx configuration is a template

`nginx/nginx.conf.template` is rendered at container start by the nginx image's
envsubst step. Only four placeholders are substituted: `${MINE}` (webapp path,
`/var/www/${MINE}` alias) and `${PUBLIC_HOST}`, `${PUBLIC_SCHEME}`,
`${PUBLIC_PORT}`, which `nginx/public-url.envsh` derives from `PUBLIC_URL`.
Everything else, including nginx's own `$host`, `$uri`, `$1`, is left alone.
The container refuses to start if `PUBLIC_URL` or `MINE` is missing. To see the
rendered file: `docker compose exec nginx cat /etc/nginx/conf.d/default.conf`.

`X-Forwarded-Proto` and `X-Forwarded-Port` are taken from `PUBLIC_URL`, so a
test deployment reached over plain `http://host:port` gets the right values
automatically.

nginx also answers `/favicon.ico` itself, proxying the mine's icon from Tomcat
(`/${MINE}/model/images/favicon.ico`). BlueGenes' own handler for that path
redirects the browser to `BLUEGENES_BACKEND_SERVICE_ROOT`, i.e. to
`http://tomcat:8080/...`, which the browser cannot reach and blocks as mixed
content (visible as a console error on every page). Check with
`curl -sI https://www.humanmine.org/favicon.ico`: expect `200` and
`Content-Type: image/x-icon`, not a `302` to an internal host.

## BlueGenes tools and `imjs` (blank JBrowse / visualizer panels)

The BlueGenes npm tools (JBrowse, protein visualizer, MSA viewer, the
expression visualizers, the Cytoscape network) are built against the
InterMine JavaScript client as a browser global, `window.imjs`. BlueGenes
used to load it from `cdn.intermine.org` on every page. Commit 06363a30 on
its `dev` branch (October 2024, merged January 2025, so in any image built
from `dev` since, including `1.4.5-dx`) removed that script tag because the
CDN had died, on the assumption that nothing used it. Since then every tool
throws `ReferenceError: imjs is not defined` and leaves an empty panel; the
JBrowse section of a gene report page is the most visible casualty.

Until BlueGenes ships the client itself, the mine's nginx fills the gap:
`nginx/vendor/imjs/im.min.js` (imjs 4.0.0, pinned, see the README next to
it) is served at `/vendor/imjs/im.min.js`, and `location /` uses
`sub_filter` to add `<script src="/vendor/imjs/im.min.js">` before `</head>`
of every BlueGenes HTML page. That needs the upstream body uncompressed
(`proxy_set_header Accept-Encoding ""`), so the server block turns `gzip on`
for what it sends to clients. Check:

```sh
curl -s https://www.humanmine.org/humanmine/report/Gene/1234788 | grep -o '<script src="/vendor/imjs/im.min.js"></script>'
curl -sI https://www.humanmine.org/vendor/imjs/im.min.js | head -1      # 200
```

and a gene report page should show JBrowse and the other visualizers. If
BlueGenes starts providing `imjs` again, delete the `location /vendor/`
block, the two `sub_filter` lines and the `Accept-Encoding` line.

## BlueGenes tools that call dead external services

Two of the installed tools fail even with `imjs` present, because each
depends on a web service outside the mine that no longer answers. Both are
fixed by editing the tool's built bundle in place in `data/tools/` (the
directory mounted into the BlueGenes container as `/tools`); no container
needs restarting, but browsers may cache the old bundle, so reload with
cache bypass. Reinstalling or updating a tool from the BlueGenes admin page
restores the original bundle, so keep a note of these edits.

**Protein Visualizer** (`@intermine/bluegenes-protein-visualizer` 2.1.1)
looks structures up with RCSB's search API v1, switched off in July 2022.
The v2 endpoint takes the same query and returns the same fields:

```sh
for f in $(grep -rl 'rcsbsearch/v1/query' data/tools/); do
    cp "$f" "$f.bak-rcsb"; sed -i 's#rcsbsearch/v1/query#rcsbsearch/v2/query#' "$f"
done
```

**Multiple Sequence Alignment Viewer** (`@intermine/bluegenes-msa-viewer`
2.1.1) posts the proteins' sequences to
`https://sequencealignment.apps.intermine.org`, which InterMine no longer
hosts, and its bundled `msa` library also crashes in every browser released
since 2021. [intermineorg/sequence-alignment-service](https://github.com/intermineorg/sequence-alignment-service)
runs the service as a container shared by all mines on a host; this
template's nginx proxies `/align-sequence` to it (the `$aligner` upstream),
and that repository's README gives the two-line patch that points the tool
at the same-origin path and fixes the library bug.

The remaining visualizers (tissue expression, GO terms, pathways,
interactions, Cytoscape) talk only to the mine itself and need nothing.

## Parallel test deployment

Two deployments of the same mine can run on one host: every container name
carries `${INSTANCE}` from `.env`, the networks and the `tomcat-webapps` volume
are namespaced by the compose project (the directory name), and only nginx
publishes a port. Postgres, Solr and Tomcat all resolve `tomcat`, `postgres`,
`solr` within their own project network, so the mine properties need no edits
beyond what the builder entrypoint does for `webapp.baseurl`.

The examples use FlyMine, with production in `~/intermine/docker/flymine` and
the test copy in `~/intermine/docker/flymine-test` (a different directory name
is required, because it becomes the compose project name). For HumanMine
substitute `humanmine` throughout.

1. Copy the configuration, not the data:

   ```
   rsync -a --exclude data/ ~/intermine/docker/flymine/ ~/intermine/docker/flymine-test/
   cd ~/intermine/docker/flymine-test
   ```

2. Edit `.env`:

   ```
   MINE=flymine
   INSTANCE=_test
   HTTP_PORT=14809
   GID=1010
   PUBLIC_URL=http://<host>:14809      # or https://test.flymine.org if the front proxy routes it
   ```

3. Create the data directory. Symlinks are fine for shared, read-mostly caches
   and must NOT be used for anything a running service writes or locks:

   | `data/…`              | test copy | why                                                                   |
   |-----------------------|-----------|-----------------------------------------------------------------------|
   | `maven.m2`            | symlink   | dependency cache, safe to share                                       |
   | `tools`               | symlink   | BlueGenes tools, read-only at run time                                |
   | `properties.intermine`| copy      | the entrypoint rewrites `webapp.baseurl`; a symlink would change prod |
   | `workdir`             | copy      | Gradle writes build output here; sharing would collide with prod builds |
   | `solr`                | copy      | Solr holds write locks on its cores; two instances on one dir corrupt it |
   | `postgres`            | copy      | two postmasters on one data dir is a hard failure / corruption; copy must be consistent |

   Docker follows symlinks on the host side, so a symlinked `data/postgres`
   would mount the production directory into the test postgres. Do not.

   ```
   make volumes
   rmdir data/maven.m2 data/tools
   ln -s ~/intermine/docker/flymine/data/maven.m2 data/maven.m2
   ln -s ~/intermine/docker/flymine/data/tools    data/tools
   rsync -a ~/intermine/docker/flymine/data/properties.intermine/ data/properties.intermine/
   rsync -a ~/intermine/docker/flymine/data/workdir/              data/workdir/
   rsync -a ~/intermine/docker/flymine/data/solr/                 data/solr/      # index is quiescent in production
   ```

   Postgres needs ownership preserved (it runs as uid 29023) and a consistent
   snapshot, so run as root and take the final pass with production stopped:

   ```
   sudo rsync -a ~/intermine/docker/flymine/data/postgres/ data/postgres/            # bulk copy, production running
   (cd ~/intermine/docker/flymine && make stop)
   sudo rsync -a --delete ~/intermine/docker/flymine/data/postgres/ data/postgres/   # short delta pass
   (cd ~/intermine/docker/flymine && make start)
   sudo chmod 700 data/postgres
   ```

   Alternatives with no downtime: `pg_dump` from the production container and
   restore into the test one (slow for a mine-sized database), or
   `cp -a --reflink=always` on btrfs/XFS (instant, but still needs production
   stopped for a consistent copy).

4. Build and start:

   ```
   make build
   make up
   docker compose logs nginx        # shows the derived PUBLIC_HOST/SCHEME/PORT
   ```

5. Deploy the webapp into the (empty) test Tomcat. `make shell` applies
   `webapp.baseurl` on the way in:

   ```
   make shell
     cd workdir/${MINE}            # wherever the mine's gradle project is
     ./gradlew cargoDeployRemote   # first deploy; later: ./gradlew cargoRedeployRemote
   ```

6. Test at `PUBLIC_URL`: `make check-baseurl`, generate code from a results page,
   log in, share a list. Nothing in the test stack talks to production.

7. Tear down: `make down` then `rm -rf data` (removes the symlinks, not their
   targets). Memory: the test stack needs the same RAM as production
   (Solr 8 GB, Tomcat 4 GB, the builder up to 32 GB while building).

## JBrowse (genome browser)

InterMine does not run JBrowse; report pages embed a separately hosted
JBrowse 1.x (by default `https://jbrowse.intermine.org/`) and point it at the
mine's `service/jbrowse/...` endpoints. If that host is unreachable every gene
report page shows a blank browser panel. One JBrowse serves every mine, so it
lives in its own repository:
[intermineorg/jbrowse](https://github.com/intermineorg/jbrowse).

What this template contributes is the optional `/jbrowse/` location in
`nginx/nginx.conf.template`: when the `jbrowse` container is joined to this
deployment's `frontend` network, the mine serves it as
`${PUBLIC_URL}/jbrowse/` with no extra port or hostname. The upstream is a
runtime-resolved variable, so nginx starts and serves the mine whether or not
that container exists. The steps (network override, tool `config.json`
edit) are in that repository's README.

## License

MIT, see [LICENSE](LICENSE).
