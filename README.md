## General commands

`make up` to bring up services (postgres, solr, tomcat)

`make shell` to start a shell in container with:
- environment needed to build a mine
- *data/flymine* and more volumes mounted to homedir
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

`make restart` to restart containers

To temporarily stop and later resume the containers without recreating them, use
`make stop` and `make start`
