> This note was written while the changes were being made to a copy of the
> original template (then called `fixed/template/`); in this repository the
> template *is* the repository root, and `git log` replaces the diff file.

# Fixing the URL embedded in generated code (HumanMine / FlyMine Docker template)

`fixed/template/` is `template.tar.gz` with the changes described here applied.
`fixed/template.diff` is the unified diff against the original (the nginx
config shows as a removed file plus a new `.template`).

## TL;DR

The URL in generated code does not come from the request at all. InterMine
builds it from the mine property **`webapp.baseurl`**, and in this deployment
that property holds the Docker-internal Tomcat URL (the same value as
`webapp.deploy.url`). nginx already forwards the public `Host` and
`X-Forwarded-*` headers, but InterMine ignores them once `webapp.baseurl` is
set, which is why proxy-level fixes have no effect.

The fix is to set `webapp.baseurl` to the public origin
(`https://www.flymine.org`, `https://www.humanmine.org`) while leaving
`webapp.deploy.url` at `http://tomcat:8080`, then redeploy the webapp. The
template now derives that value from a single `PUBLIC_URL` setting in `.env`
and enforces it automatically. As a complementary hardening step, Tomcat is
given a `RemoteIpValve` so its own view of the request (scheme, port, client
IP) matches the public request.

## Root cause, with evidence from the upstream sources

The chain, verified against the current `intermine/intermine`, `im-tables-3`
and `bluegenes` repositories on GitHub:

1. **The "Generate code" dialog is a web-service call made by the browser.**
   BlueGenes embeds im-tables; its `:main/generate-code` event calls
   `imcljs` `fetch/code`, which requests
   `GET <service root>/query/code?lang=py&query=...` (`im_tables/events.cljs`,
   lines 719 to 727). The service root is `BLUEGENES_DEFAULT_SERVICE_ROOT`, the
   public URL, so the request reaches Tomcat through nginx. The legacy JSP
   webapp's "Generate code" link (`WebserviceCodeGenAction`) ends in the same
   place.

2. **The server decides the embedded URL.** `CodeService.execute()` does

   ```java
   String serviceBaseURL = new URLGenerator(request).getPermanentBaseURL();
   ```

   and the Python generator emits
   `service = Service("<serviceBaseURL>/service")`
   (`WebservicePythonCodeGenerator.java`, line 128). Perl, Ruby, Java and
   JavaScript generators use the same value.

3. **`URLGenerator` prefers the property over the request.**
   `intermine/webapp/src/main/java/org/intermine/web/util/URLGenerator.java`:

   ```java
   String baseUrl = webProperties.getProperty("webapp.baseurl");
   if (StringUtils.isEmpty(baseUrl)) {
       return getCurrentURL(request, contextPath);        // scheme://host[:port]/context
   }
   if (request.getServerPort() != 80 && request.getServerPort() != 443) {
       baseUrl += ":" + request.getServerPort();
   }
   String path = webProperties.getProperty("webapp.path");
   return new URL(baseUrl + "/" + path).toString();
   ```

   With `webapp.baseurl=http://tomcat:8080` (or the stock
   `http://localhost:8080`) the result is `http://tomcat:8080/flymine`, exactly
   the symptom. The `Host` header nginx forwards is never consulted.

4. **The property is baked into the WAR.** The Gradle `mergeProperties` task
   appends `~/.intermine/<mine>.properties` to `WEB-INF/web.properties`
   (`plugin/.../WebAppPlugin.groovy`, lines 42 to 56). Changing the file in
   `data/properties.intermine/` therefore does nothing until the webapp is
   rebuilt and redeployed (`./gradlew cargoRedeployRemote`); a Tomcat restart is not
   enough.

5. **Why it is wrong in the first place.** The stock example properties ship
   with both `webapp.deploy.url=http://localhost:8080` and
   `webapp.baseurl=http://localhost:8080`, and the standard InterMine Docker
   builder only rewrites `webapp.deploy.url` to `http://tomcat:8080`. Nothing
   in the pipeline ever sets `webapp.baseurl` to the public address, so it
   stays internal. The two properties mean different things:

   | Property            | Meaning                                                | Correct value here       |
   |---------------------|--------------------------------------------------------|--------------------------|
   | `webapp.deploy.url` | Tomcat manager URL that Gradle pushes the WAR to       | `http://tomcat:8080`     |
   | `webapp.baseurl`    | Public origin InterMine writes into URLs it hands out  | `https://www.flymine.org`|

`webapp.baseurl` is also read directly (not via `URLGenerator`) by the OAuth2
login flow, list-sharing invitation e-mails, password-reset mails, the JBrowse
displayer, `bagDetails.jsp` and the query-schema location in
`AbstractQueryService`. All of those are currently emitting the internal URL
as well; the fix below repairs them at the same time.

## Options considered

### A. Set `webapp.baseurl` to the public origin (InterMine's own knob)

Edit `data/properties.intermine/<mine>.properties`, keep `webapp.deploy.url`
internal, run `./gradlew cargoRedeployRemote`.

- Pro: this is the mechanism InterMine designed for exactly this situation.
  One line fixes code generation in every language plus the dozen other
  places that read the property. No code changes, no new moving parts.
- Pro: independent of how many proxies sit in front of Tomcat.
- Con: a hostname is now configured in two places (nginx and the mine
  properties) and must be kept consistent. The value must be the bare origin;
  InterMine appends `/<webapp.path>` and `/service` itself, so a trailing
  slash or a path would produce `https://host//flymine` or `https://host/flymine/flymine`.
- Con: on its own it is exactly the step that was forgotten before. Without
  something in the template enforcing it, a freshly copied properties file
  reintroduces the bug.
- Minor quirk: `URLGenerator` still appends the request port when it is not
  80 or 443. Today nginx sends `Host: $host` without a port and Tomcat sees
  port 80, so nothing is appended; option C removes the dependence on that
  coincidence.

### B. Blank `webapp.baseurl` and let InterMine derive the URL from the request

Leave the property empty so `getCurrentURL()` runs, and add a Tomcat
`RemoteIpValve` so scheme and port come from `X-Forwarded-Proto/Port`.

- Pro: no hostname configured in the mine; works for every alias the site is
  served under.
- Con, decisive: the many direct readers of `webapp.baseurl` listed above do
  not have a request-based fallback. OAuth2 redirect URIs, sharing e-mails and
  the JBrowse config URL would become `null/flymine/...`. This option trades
  one bug for several.
- Con: correctness now depends on every proxy hop forwarding the right headers.

### C. A plus a Tomcat `RemoteIpValve` (chosen)

Set the property as in A, and also make Tomcat honour the `X-Forwarded-For`,
`X-Forwarded-Proto` and `X-Forwarded-Port` headers nginx already sends.

- Pro: everything in A.
- Pro: Tomcat's `request.getScheme()`, `getServerPort()`, `isSecure()` and
  `getRemoteAddr()` now describe the public request. That removes the
  port-append quirk, makes Struts and servlet redirects use `https://`, lets
  `Secure` cookies be set correctly, and puts real client IPs in the access
  log. It also makes the request-derived fallback in `URLGenerator` correct,
  should the property ever be blank.
- Pro: standard Tomcat configuration, one XML element, no code changes;
  headers are only trusted from RFC1918 and loopback source addresses, which
  is what the Docker bridge networks use, so they cannot be spoofed from
  outside.
- Con: nginx hard-codes `X-Forwarded-Proto https`, so Tomcat will believe
  every request is HTTPS, including a direct hit on the published plain-HTTP
  port. That assumption is already built into the existing nginx config
  (TLS is terminated by a proxy in front of it) and has no effect on the
  embedded URL once `webapp.baseurl` is set.
- The alternative of setting `proxyName`/`proxyPort`/`scheme="https"` on the
  Tomcat connector instead of the valve achieves a similar result statically,
  but hard-codes the hostname in `server.xml` as well and loses the real
  client IP. The valve is preferable.

### D. Rewrite the response body in nginx (`sub_filter`)

Replace `http://tomcat:8080/flymine` with the public URL in responses from
`/flymine/service/query/code`.

- Pro: no redeploy of the webapp.
- Con: papers over a misconfiguration instead of fixing it; must disable
  upstream compression (`proxy_set_header Accept-Encoding ""`), enumerate
  content types (`text/plain`, `application/json`), handle JSON-escaped
  variants, and be repeated for every endpoint that leaks the URL (permalinks,
  e-mails cannot be fixed this way at all). Fragile and invisible to the next
  maintainer. Not recommended.

### E. Change only nginx (`Host`, `X-Forwarded-*`)

- Already done in the template, and demonstrably insufficient: `URLGenerator`
  ignores the request whenever `webapp.baseurl` is set. This is probably why
  the problem persisted despite a correct-looking proxy config.

### F. Patch InterMine so `URLGenerator` honours `X-Forwarded-*` / ignores the property

- Pro: would make the behaviour "just work" for everyone behind a proxy.
- Con: requires forking and rebuilding InterMine (or waiting for an upstream
  release), and still leaves the direct readers of `webapp.baseurl` broken.
  Nothing it would achieve is out of reach of option C. Not recommended for
  this deployment; worth an upstream issue at most.

### G. Fix it client-side in BlueGenes / im-tables

- Not possible without forking im-tables, and would not help the JSP webapp,
  the Perl/Python/Java/JavaScript generators invoked over the API, or any of
  the other URL consumers. Not recommended.

**Clearly superior: option C.** A is the actual fix and is mandatory; the
valve costs one XML element and removes the remaining ways for Tomcat's
internal view of the request to leak into URLs. The template changes below
implement C and, importantly, make A impossible to forget.

## Changes made in `fixed/template/`

### `.env`
Added `PUBLIC_URL=https://www.somemine.org`, the single source of truth for
the public origin (scheme + host, optional port, no path, no trailing slash).
Replace it per site, e.g. `https://www.flymine.org`. Also added `INSTANCE=`
(container-name suffix, empty in production; see "Parallel deployments").

### `docker-compose.yml`
- `intermine_builder`: new `environment` block passing `MINE_NAME=${MINE}` and
  `WEBAPP_BASEURL=${PUBLIC_URL}` into the builder container.
- `bluegenes`: `BLUEGENES_DEFAULT_SERVICE_ROOT=${PUBLIC_URL}/${MINE}`, so the
  browser-side service root comes from the same setting.
- Bug fixed on the way: the original lines were written as
  `- BLUEGENES_DEFAULT_SERVICE_ROOT="https://${MINE}.org/${MINE}"`. In the
  list form of `environment`, docker-compose keeps the quotes as part of the
  value; `docker compose config` on the original shows
  `'"https://somemine.org/somemine"'`. Both BlueGenes variables are now
  unquoted. (The production files presumably differ or BlueGenes would not
  start; check them.)

### `intermine_builder/set-webapp-baseurl.sh` (new)
Idempotent POSIX-sh script installed as `/usr/local/bin/set-webapp-baseurl`.
It validates `WEBAPP_BASEURL` (must be `scheme://host[:port]`, trailing slash
stripped, path rejected), then sets or appends `webapp.baseurl=` in
`~/.intermine/${MINE_NAME}.properties`, which is the bind-mounted
`data/properties.intermine/` directory. It never touches `webapp.deploy.url`.
It prints what it changed and reminds you to redeploy. Exit codes: 0 done or
already correct, 1 bad input, 2 properties file not there yet.

Tested with dash and with busybox `ash`/`sed` in an Alpine container running
as a non-root user (replace, append, idempotent re-run, rejected inputs,
missing file).

### `intermine_builder/entrypoint.sh` (new) and `intermine_builder.Dockerfile`
The image now has an `ENTRYPOINT` that runs `set-webapp-baseurl` on every
container start (so `make shell` keeps the property correct) and then execs
the requested command; `CMD` is still `/bin/sh`. If `WEBAPP_BASEURL` is unset
or the properties file does not exist yet it only prints a notice and
continues, so first-time setup is unaffected. When the command itself is
`set-webapp-baseurl` (the `make baseurl` target) it is not run twice.

### `tomcat/configs/server.xml`
Added inside `<Host>`, before the access log valve:

```xml
<Valve className="org.apache.catalina.valves.RemoteIpValve"
       remoteIpHeader="X-Forwarded-For"
       protocolHeader="X-Forwarded-Proto"
       portHeader="X-Forwarded-Port"
       internalProxies="10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|169\.254\.\d{1,3}\.\d{1,3}|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.1[6-9]{1}\.\d{1,3}\.\d{1,3}|172\.2[0-9]{1}\.\d{1,3}\.\d{1,3}|172\.3[0-1]{1}\.\d{1,3}\.\d{1,3}|0:0:0:0:0:0:0:1|::1" />
```

`internalProxies` is Tomcat's default set written out explicitly; it covers
all Docker bridge address pools. The existing `AccessLogValve` gained
`requestAttributesEnabled="true"` so `%h` logs the recovered client IP.

### `nginx/nginx.conf.template`, `nginx/public-url.envsh`, `nginx.Dockerfile`
The nginx config was FlyMine-specific: `flymine` appeared in the proxied
paths (`/flymine/service`, `/flymine/model`, `/query` to `/flymine`, the
`/var/www/flymine` alias) and `flymine.leoank.me` was the `server_name`.
`leoank.me` is a personal domain of the person who wrote this Docker setup
(`LABEL maintainer="Ank"`), evidently a staging host; the commented-out
blocks were its port-80 to HTTPS redirects and Let's Encrypt challenge
location, which cannot apply here because TLS is terminated in front of this
container. With a single `server` block nginx answers for any `Host`, so the
stale name was inert, but the log file names and any uncommented redirect
would have pointed at the wrong host.

The file is now `nginx.conf.template`, rendered at start-up by the nginx
image's envsubst step with `${MINE}`, `${PUBLIC_HOST}`, `${PUBLIC_SCHEME}` and
`${PUBLIC_PORT}`; the last three are derived from `PUBLIC_URL` by
`public-url.envsh`, which is sourced first and aborts the container if
`PUBLIC_URL` or `MINE` is missing. `NGINX_ENVSUBST_FILTER` restricts
substitution to those four names so nginx's own `$host`, `$uri`, `$1` are
untouched. `X-Forwarded-Proto` and `X-Forwarded-Port` now come from
`PUBLIC_URL` instead of being hard-coded to `https`/`443`, which is what makes
a plain-HTTP test deployment behave correctly behind the `RemoteIpValve`.
The dead commented-out blocks were dropped. The `/come` location (a second
webapp on Tomcat) was kept as is. Verified by building the image and running
`nginx -t` against dummy `tomcat`/`bluegenes` containers with both an HTTPS
and an `http://host:port` `PUBLIC_URL`.

Upstreams are now variables (`set $tomcat http://tomcat:8080`) with
`resolver 127.0.0.11 valid=10s`, so nginx resolves them through Docker DNS at
request time instead of once at start-up. Verified with echo servers in place
of tomcat and bluegenes: nginx starts before they exist, forwards
`/query/results.do?x=1` as `/flymine/results.do?x=1`, `/flymine/service/...`
and `/come/...` unchanged, `/` to bluegenes, and keeps working after tomcat is
recreated on a different IP with no nginx restart. See "Container restarts"
below.

### `/favicon.ico` (same class of leak, found later)

BlueGenes' server route for `/favicon.ico` (`src/clj/bluegenes/routes.clj`)
replies `302 Found` to `(get-service-root env) + "/model/images/favicon.ico"`,
and `get-service-root` prefers `BLUEGENES_BACKEND_SERVICE_ROOT` over the public
`BLUEGENES_DEFAULT_SERVICE_ROOT`. In this deployment the backend root is
`http://tomcat:8080/${MINE}`, so every page sends the browser to an internal
hostname, over plain http, which it blocks as mixed content. The upstream fix
would be for that one route to use the default (public) service root; until
then `nginx.conf.template` has `location = /favicon.ico` that rewrites to
`/${MINE}/model/images/favicon.ico` and proxies Tomcat directly. No redirect,
same origin, and the browser caches it for a day. BlueGenes' comment in
`routes.clj` says a `public/favicon.ico` resource would take priority, but that
means rebuilding the BlueGenes jar, which the nginx rule avoids.

### `imjs` for the BlueGenes tools (found later, unrelated to URLs)

Every BlueGenes npm tool (`@intermine/bluegenes-jbrowse-tool` and the rest)
calls `imjs.Service` from a global that BlueGenes was expected to provide.
BlueGenes commit 020b53a7 (2021) added
`<script src="https://cdn.intermine.org/js/intermine/imjs/latest/im.min.js">`
for exactly that reason; commit 06363a30 (PR #844, "Remove script loading
imjs from cdn", merged 2025-01-26) removed it with the note that it "doesn't
seem like it's in use either (not referenced in code and doesn't break
anything)". It is not referenced in BlueGenes' own code, but it is in every
tool's, so on images built from `dev` after that date all tools fail with
`imjs is not defined`. Verified by loading a HumanMine gene report page in
headless Chromium: the tool container is created at 600px and stays empty,
and the console shows the ReferenceError from each tool bundle.

Fix in this template: the nginx image carries `vendor/imjs/im.min.js`
(imjs 4.0.0 from npm, SHA-256 pinned, the version the tool template
targets) and `location /` injects one script tag with `sub_filter`. Tested
end to end through the template nginx against the live HumanMine: `imjs`
defined, the JBrowse iframe created, JBrowse rendering tracks, and the other
seven tools rendering. The proper fix is upstream: BlueGenes should bundle
imjs and set `window.imjs`, or the tools should bundle it themselves.

### Parallel deployments: `INSTANCE`
Container names are now `${MINE}${INSTANCE}_<service>`, with `INSTANCE` empty
in production. Networks and the `tomcat-webapps` volume are already namespaced
by compose project (directory name) and only nginx publishes a port, so a copy
of the directory with `INSTANCE=_test`, another `HTTP_PORT` and its own
`PUBLIC_URL` runs alongside production. The README has the step-by-step
procedure, including which `data/` entries may be symlinked (`maven.m2`,
`tools`) and which must be copies (`postgres`, `solr`, `properties.intermine`,
`workdir`).

### `Makefile`
- `-include .env` so targets can use `MINE` and `PUBLIC_URL`.
- `make baseurl`: runs `set-webapp-baseurl` in a throw-away builder container.
- `make check-baseurl`: performs the same `/service/query/code` request the
  dialog makes against the live site and checks that the embedded URL equals
  `${PUBLIC_URL}/${MINE}/service`.

### `README.md`
New sections explaining `PUBLIC_URL`, the `deploy.url` versus `baseurl`
distinction, the redeploy requirement, the two new targets, the nginx
template, and how to stand up a parallel test deployment.

## Rolling it out on an existing site

1. Set `PUBLIC_URL` in `.env` (`https://www.flymine.org` or
   `https://www.humanmine.org`).
2. `make build` (builder and tomcat images changed), then `make up` to recreate
   the Tomcat container with the new `server.xml` and BlueGenes with the new
   service root.
3. `make baseurl` (or `make shell`) and read the output. Confirm with
   `grep -E 'webapp\.(baseurl|deploy\.url|path)' data/properties.intermine/<mine>.properties`
   that `webapp.deploy.url` is still `http://tomcat:8080` and `webapp.baseurl`
   is the public origin.
4. In `make shell`, from the mine's Gradle project: `./gradlew cargoRedeployRemote`.
   This rebuilds the webapp with the new properties; no database work.
5. `make check-baseurl`, or generate code from any results page and look at
   the `Service(...)` line.

## Assumptions and things noticed but left alone

- The actual production `<mine>.properties` files are not in the tarball (they
  live in the `data/properties.intermine` volume). The analysis assumes
  `webapp.baseurl` there is `http://tomcat:8080` or `http://localhost:8080`;
  either produces the reported symptom, and step 3 above shows the real value.
- The JSP webapp is published under `/query` while its context path is
  `/${MINE}`; with the explicit `webapp.baseurl` the generated
  `https://www.flymine.org/flymine/service` still routes correctly through the
  `/${MINE}/service` location.
- nginx writes its access and error logs to per-host files inside the
  container (as before), so they do not appear in `docker compose logs`.

## Container restarts and Solr stability

Question answered: can containers be restarted individually, and why does
Solr go unhealthy. Both were checked against the real coupling in the code,
not assumed.

**Coupling found.**
- nginx resolved `tomcat`/`bluegenes` once at start-up (literal `proxy_pass`),
  so it needed a restart after either was recreated and could not start
  without them. Fixed in the nginx template with runtime DNS resolution.
- The InterMine webapp uses HikariCP for its read pool (recovers from a
  Postgres restart), but `ObjectStoreWriterInterMineImpl` holds one
  long-lived connection and only re-validates it when
  `osw.*.robustConnection=true`, which nothing sets by default. So Postgres
  must be followed by Tomcat.
- The webapp's Solr access is a cached SolrJ `HttpSolrClient` by hostname,
  stateless HTTP, so Solr restarts alone.
- Nothing depends on nginx or bluegenes internally.

**Solr.** `bin/solr` 8.4.1 never reads `JAVA_OPTS`; heap comes only from
`SOLR_HEAP`/`SOLR_JAVA_MEM`, default 512m, with `-XX:OnOutOfMemoryError`
running `oom_solr.sh`, which kills the process. Running the image with the
template's `MEM_OPTS`/`JAVA_OPTS` and `solr start -f -V` shows
`-Xms512m -Xmx512m`; with `SOLR_JAVA_MEM` it shows `-Xms1g -Xmx8g`. A 512 MB
Solr serving a FlyMine-sized faceted index either GC-thrashes until the ping
healthcheck times out (unhealthy, process alive) or OOMs, kills itself and is
restarted by Docker (unhealthy while the core reopens). Docker never restarts
on unhealthy alone.

**Changes.**
- `docker-compose.yml`: `version: "3.4"`; Solr gets `SOLR_JAVA_MEM=-Xms1g -Xmx8g`
  and a healthcheck with `start_period: 180s`, 5 retries; Tomcat gets
  `MINE` in its environment and `start_period: 900s`.
- `solr/solr.Dockerfile`: the ineffective `MEM_OPTS`/`JAVA_OPTS` lines removed.
- `tomcat/tomcat.Dockerfile`: the healthcheck used `wget`, which the image does
  not have, so it failed on every run; replaced with a bash `/dev/tcp` request
  for `/${MINE}/service/version`. Tested against a Tomcat container: 200 passes,
  404 and closed port fail.
- `Makefile`: `restart-solr`, `restart-tomcat`, `restart-bluegenes`,
  `restart-nginx` (single service), `restart-postgres` (postgres, wait for
  healthy, then tomcat), `wait-healthy-<service>` (15 minute cap, fails fast
  if the container does not exist) and `ps`. `restart` still restarts
  everything.
- `README.md`: a dependency table per service, what a restart does not do
  (config/image changes need `make up`), the Solr diagnosis commands and the
  memory caveat (8 GB Solr + 4 GB Tomcat + Postgres + a 32 GB build must fit
  in host RAM, otherwise the kernel OOM killer becomes the next cause).

