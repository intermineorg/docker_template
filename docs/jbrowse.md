# JBrowse for InterMine: where it runs and how to host it

## What the documentation says

InterMine does not run JBrowse itself. The mine only provides the data
side of JBrowse's REST specification, and a separately hosted JBrowse 1.x
reads from it. The current InterMine documentation
(`im-docs/docs/webapp/jbrowse.md`, published at
<https://intermine.readthedocs.io/en/latest/webapp/jbrowse/>) says:

- "You will need an installation of JBrowse for this task." It was tested
  with JBrowse 1.16.4 and links to the JBrowse 1.x install tutorial.
- The one required edit to that installation is in `index.html`:
  `data-config='"allowCrossOriginDataRoot": true'`, because the mine's data
  lives on a different origin from the JBrowse page.
- Report pages get JBrowse through the BlueGenes tool
  `@intermine/bluegenes-jbrowse-tool`. Its `config.json` ships with
  `"jbrowseUrl": "https://jbrowse.intermine.org/?data="`, and the docs say
  to replace that with the URL of your own installation.
- Optionally, each mine is listed as a dataset in the installation's
  `jbrowse_conf.json`, so the dataset drop-down in JBrowse's menu bar can
  switch between mines.

The InterMine 4 documentation covered the classic (JSP) webapp as well:
the report displayer `jbrowseDisplayer.jsp` reads `jbrowse.install.url`,
whose built-in default in `bio.web.properties` is
`https://jbrowse.intermine.org/`.

So every mine in the world that did not override those defaults depends on
`jbrowse.intermine.org`. Nothing in the documentation says how that host
itself was deployed, and it is not in the Docker template. It was simply a
static JBrowse 1.x checkout behind a web server.

## How the pieces fit together

```
browser ──► https://www.humanmine.org/...report page (BlueGenes or JSP)
               │ iframe
               ▼
         https://jbrowse.intermine.org/?data=https://www.humanmine.org/humanmine/service/jbrowse/config/9606
                                       &loc=1:1000000..1100000&tracks=Humanmine-9606-Gene
               │ XHR (CORS, allowed by the mine: Access-Control-Allow-Origin)
               ▼
         https://www.humanmine.org/humanmine/service/jbrowse/config/9606/trackList.json
         https://www.humanmine.org/humanmine/service/jbrowse/config/9606/seq/refSeqs.json
         https://www.humanmine.org/humanmine/service/jbrowse/9606/features/<chr>?start=..&end=..&type=Gene
         https://www.humanmine.org/humanmine/service/jbrowse/9606/stats/...
```

The `baseUrl` values inside `trackList.json` are built from
`webapp.baseurl`, which is why that property had to be the public URL
before JBrowse could ever work from a Docker deployment. With that fixed,
the only missing piece is the JBrowse host.

## The `jbrowse/` service in this repository

`jbrowse/` is a self-contained compose stack, independent of the mines:

| File | Purpose |
|------|---------|
| `jbrowse/Dockerfile` | Downloads the JBrowse 1.16.11 release (SHA-256 pinned), sets `allowCrossOriginDataRoot` to `true`, serves it with nginx. |
| `jbrowse/default.conf` | nginx static-site config. Sends no `X-Frame-Options`, so mines can iframe it. |
| `jbrowse/jbrowse_conf.json` | The dataset drop-down: one entry per mine. |
| `jbrowse/docker-compose.yml`, `jbrowse/.env` | One container, `jbrowse`, published on `JBROWSE_HTTP_PORT` (14880). |

Because it serves the default URL, restoring `https://jbrowse.intermine.org/`
needs **no change to any mine**: no property edit, no redeploy, no tool
configuration.

### 1. Start the container

```sh
cd ~/intermine/docker/docker_template/jbrowse     # or wherever this repo is checked out
docker-compose up -d --build
curl -s http://localhost:14880/ | grep -o 'allowCrossOriginDataRoot": [a-z]*'   # expect: true
```

Change the port in `jbrowse/.env` if 14880 is taken.

### 2. DNS

Create an `A` record (and `AAAA` if the server has IPv6) for
`jbrowse.intermine.org` pointing at the server that hosts HumanMine and
FlyMine.

### 3. TLS and the front end

The mine containers listen on plain HTTP (`HTTP_PORT` in each `.env`) and
something on the host already terminates TLS for `www.humanmine.org` and
`www.flymine.org`. Add `jbrowse.intermine.org` to that same front end,
proxying to port 14880. The page must be served over HTTPS: the mines are
HTTPS, and browsers refuse to load an HTTP iframe inside an HTTPS page.

If the front end is nginx with certbot:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name jbrowse.intermine.org;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name jbrowse.intermine.org;

    ssl_certificate     /etc/letsencrypt/live/jbrowse.intermine.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/jbrowse.intermine.org/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:14880;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

```sh
sudo certbot --nginx -d jbrowse.intermine.org
sudo nginx -t && sudo systemctl reload nginx
```

With Apache, Caddy, Traefik or a load balancer the principle is the same:
a virtual host for `jbrowse.intermine.org` with a certificate, forwarding
to `127.0.0.1:14880`. Nothing about the JBrowse content depends on the
hostname, so the same container can also be reached under other names.

### 4. Check

From anywhere:

```sh
curl -sI https://jbrowse.intermine.org/ | head -1          # HTTP/2 200
curl -s  https://jbrowse.intermine.org/ | grep -o 'allowCrossOriginDataRoot": [a-z]*'
```

Then open, in a browser, the URL a HumanMine gene report page embeds:

```
https://jbrowse.intermine.org/?data=https%3A%2F%2Fwww.humanmine.org%2Fhumanmine%2Fservice%2Fjbrowse%2Fconfig%2F9606&loc=1:1000000..1100000&tracks=Humanmine-9606-Gene
```

You should see chromosome 1 with a Gene track, and the dataset drop-down
top left should offer HumanMine and FlyMine. Finally open a gene report page
on each mine. If a report page is still blank, press F12, open the Network
tab, and look for a red request: it will either be the JBrowse page itself
(front-end or certificate problem) or a `service/jbrowse/...` request
(mine problem; see "Verifying the mine side" below).

## Keeping `jbrowse_conf.json` in step with the mines

The keys in `datasets` must equal the `dataset_id` each mine puts in its
`trackList.json`, which is `<project.title>-<taxonId>`. HumanMine's is
`Humanmine-9606` (verified). Check FlyMine's with:

```sh
curl -s https://www.flymine.org/flymine/service/jbrowse/config/7227/trackList.json | grep -o '"dataset_id":"[^"]*"'
```

and correct the key in `jbrowse/jbrowse_conf.json` if it differs. After an
edit, `docker-compose up -d --build` in `jbrowse/`. A wrong key only
affects which entry the drop-down highlights; tracks still load.

## Alternative: serve JBrowse under each mine's own hostname (no new port)

A browser, not the server, fetches `jbrowse.intermine.org`, so no redirect
or hosts entry on the server can substitute for the public hostname. When
no new public port or hostname is available, the same container can be
reached through the mine's existing nginx as `https://www.humanmine.org/jbrowse/`
instead. This rides on the port that already serves the mine, is
same-origin with the data (so CORS is not even involved), but must be done
per mine and each mine must be told where its JBrowse is.

1. Join the jbrowse container to each mine's frontend network: copy
   `jbrowse/docker-compose.override.yml.example` to
   `jbrowse/docker-compose.override.yml`, adjust the network names
   (`docker network ls`, they are `<mine directory>_frontend`), then
   `docker-compose up -d` in `jbrowse/`.
2. The nginx template in this repository already carries a `/jbrowse/`
   location that proxies to `http://jbrowse:80` through a runtime-resolved
   variable, so the mine's nginx keeps starting and serving even when no
   jbrowse container exists (requests to `/jbrowse/` then return 502).
   Rebuild the mine's nginx (`docker-compose up -d --build nginx`) and check:

   ```sh
   curl -sI https://www.humanmine.org/jbrowse/ | head -1                       # 200
   curl -s  https://www.humanmine.org/jbrowse/ | grep -o 'allowCrossOriginDataRoot": [a-z]*'
   ```

3. Point the mine at it:
   - BlueGenes: edit `jbrowseUrl` in the installed tool's `config.json`
     (under the tools directory mounted at `./data/tools`; find it with
     `find data/tools -path '*bluegenes-jbrowse-tool/config.json'`) to
     `https://www.humanmine.org/jbrowse/?data=`, then
     `docker-compose restart bluegenes`. Reinstalling or updating the tool
     from the admin page restores the default, so keep a note of the edit.
   - Classic webapp (optional): set
     `jbrowse.install.url = https://www.humanmine.org/jbrowse/` in the mine
     properties and run `./gradlew cargoRedeployRemote`.

The two routes are not exclusive: if `jbrowse.intermine.org` is published
later, the tool configuration can simply be reverted to the default.

## Verifying the mine side

These are the requests JBrowse makes. All must return JSON, not HTML, and
must carry `Access-Control-Allow-Origin`:

```sh
M=https://www.humanmine.org/humanmine/service/jbrowse
curl -sI "$M/config/9606/trackList.json"   | grep -i -E '^(HTTP|content-type|access-control-allow-origin)'
curl -sI "$M/config/9606/seq/refSeqs.json" | grep -i -E '^(HTTP|content-type)'
curl -s  "$M/config/9606/trackList.json"   | grep -o '"baseUrl":"[^"]*"' | sort -u   # must be the public URL
curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' "$M/9606/features/1?start=1000000&end=1100000&type=Gene"
```

Observed on HumanMine after the `webapp.baseurl` fix: config and feature
calls answer in about a second; `stats/global` for the whole SequenceFeature
set takes about 17 seconds, which only slows the track-density estimate for
that one catch-all track.

## Notes

- JBrowse 1.x is in maintenance; JBrowse 2 has a different configuration
  model and InterMine's REST endpoints target the 1.x specification, so
  stay on 1.16.x.
- `allowCrossOriginDataRoot` lets any page pass any `?data=` URL. That is
  the intended use here and JBrowse has no logins, but it is why the
  setting is off by default. The container serves nothing else.
- To upgrade, change `JBROWSE_VERSION` and `JBROWSE_SHA256` in the
  Dockerfile (`sha256sum` of the release zip) and rebuild.
