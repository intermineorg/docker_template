.PHONY: *

build:
	docker-compose build

shell:
	docker-compose run --rm intermine_builder

up:
	docker-compose up -d

logs:
	docker-compose logs

restart:
	docker-compose restart

start:
	docker-compose start

stop:
	docker-compose stop

down:
	docker-compose down

volumes:
	mkdir -p data/workdir data/maven.m2 data/postgres data/properties.intermine data/solr data/tools
	chmod 700 data/postgres

