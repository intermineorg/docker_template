.PHONY: *

# .env holds MINE, HTTP_PORT, GID and PUBLIC_URL (docker-compose reads it too).
-include .env

build:
	docker-compose build

shell:
	docker-compose run --rm intermine_builder

up:
	docker-compose up -d

logs:
	docker-compose logs

# Restart everything.  Usually unnecessary: see the per-service targets below
# and README "Restarting individual containers".
restart:
	docker-compose restart

# Services that can be restarted on their own.  nginx resolves tomcat/bluegenes
# at request time and the webapp talks to Solr over stateless HTTP, so nothing
# else needs to follow.
restart-solr restart-tomcat restart-bluegenes restart-nginx:
	docker-compose restart $(@:restart-%=%)

# Postgres must be followed by Tomcat: the InterMine webapp keeps one long-lived
# userprofile connection that is not re-validated (osw.*.robustConnection is
# off by default), so logins and list saving would fail until Tomcat restarts.
restart-postgres:
	docker-compose restart postgres
	$(MAKE) wait-healthy-postgres
	docker-compose restart tomcat

# Block until a container reports healthy (up to 15 minutes), e.g.
# make wait-healthy-solr
wait-healthy-%:
	@c=$(MINE)$(INSTANCE)_$*; \
	for i in $$(seq 1 180); do \
	    s=$$(docker inspect -f '{{.State.Health.Status}}' $$c 2>/dev/null) || { echo "$$c: no such container (or no healthcheck)"; exit 1; }; \
	    if [ "$$s" = healthy ]; then echo "$$c: healthy"; exit 0; fi; \
	    [ $$i -eq 1 ] || [ $$((i % 12)) -eq 0 ] && echo "$$c: $$s, waiting"; \
	    sleep 5; \
	done; \
	echo "$$c: not healthy after 15 minutes"; exit 1

# Health of every container at a glance
ps:
	docker-compose ps

start:
	docker-compose start

stop:
	docker-compose stop

down:
	docker-compose down

volumes:
	mkdir -p data/workdir data/maven.m2 data/postgres data/properties.intermine data/solr data/tools
	chmod 700 data/postgres

# Write PUBLIC_URL (from .env) into webapp.baseurl in
# data/properties.intermine/$(MINE).properties.  The builder container also does
# this automatically on every start; this target just does it explicitly.
# Afterwards redeploy the webapp from `make shell`:  ./gradlew cargoRedeploy
baseurl:
	docker-compose run --rm intermine_builder set-webapp-baseurl

# Ask the live site for generated Python code (the same web service call the
# "Generate code" dialog makes) and check which service URL it embeds.
check-baseurl:
	@url=$$(curl -sSf --get "$(PUBLIC_URL)/$(MINE)/service/query/code" \
	        --data-urlencode 'lang=py' \
	        --data-urlencode 'query=<query model="genomic" view="Gene.primaryIdentifier"/>' \
	      | sed -n 's/.*Service("\([^"]*\)").*/\1/p'); \
	echo "generated code uses: $${url:-<no Service(...) line found>}"; \
	if [ "$$url" = "$(PUBLIC_URL)/$(MINE)/service" ]; then \
	    echo "OK: matches PUBLIC_URL/MINE/service"; \
	else \
	    echo "MISMATCH: expected $(PUBLIC_URL)/$(MINE)/service"; exit 1; \
	fi
