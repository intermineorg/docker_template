FROM tomcat:8-jre8-openjdk-slim

ENV MEM_OPTS="-Xmx4G -Xms512m"
ENV GRADLE_OPTS="-server $MEM_OPTS -XX:+UseParallelGC -XX:SoftRefLRUPolicyMSPerMB=1 -XX:MaxHeapFreeRatio=99 -Dorg.gradle.daemon=false"
ENV JAVA_OPTS="$JAVA_OPTS -Dorg.apache.el.parser.SKIP_IDENTIFIER_CHECK=true $MEM_OPTS -XX:+UseParallelGC -XX:SoftRefLRUPolicyMSPerMB=1 -XX:MaxHeapFreeRatio=99"

# Intermine seems to need this to deploy.
RUN cp -avT $CATALINA_HOME/webapps.dist/manager $CATALINA_HOME/webapps/manager

COPY ./configs/* /usr/local/tomcat/conf/
COPY ./configs/web_context.xml /usr/local/tomcat/webapps/manager/META-INF/context.xml

HEALTHCHECK CMD \
  wget -qO - localhost:8080/$MINE/service/version || exit 1
