FROM solr:8.4.1-slim
LABEL maintainer="Ank"
COPY --chown=solr:solr ./scripts/intermine.sh /opt/scripts/intermine.sh
# Heap: Solr's bin/solr ignores JAVA_OPTS/MEM_OPTS.  The heap is set with
# SOLR_JAVA_MEM (or SOLR_HEAP) in docker-compose.yml; without it Solr runs on
# the 512m default and kills itself on OutOfMemoryError (bin/oom_solr.sh).
HEALTHCHECK CMD \
  wget -qO - http://localhost:8983/solr/${MINE_NAME:-biotestmine}-search/admin/ping || exit 1
EXPOSE 8983
USER $SOLR_USER
ENTRYPOINT ["docker-entrypoint.sh"]
CMD /opt/scripts/intermine.sh ${MINE_NAME:-biotestmine}
