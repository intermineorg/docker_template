FROM tomcat:8-jre8-openjdk-slim

ENV MEM_OPTS="-Xmx4G -Xms512m"
ENV GRADLE_OPTS="-server $MEM_OPTS -XX:+UseParallelGC -XX:SoftRefLRUPolicyMSPerMB=1 -XX:MaxHeapFreeRatio=99 -Dorg.gradle.daemon=false"
ENV JAVA_OPTS="$JAVA_OPTS -Dorg.apache.el.parser.SKIP_IDENTIFIER_CHECK=true $MEM_OPTS -XX:+UseParallelGC -XX:SoftRefLRUPolicyMSPerMB=1 -XX:MaxHeapFreeRatio=99"

# Intermine seems to need this to deploy.
RUN cp -avT $CATALINA_HOME/webapps.dist/manager $CATALINA_HOME/webapps/manager

COPY ./configs/* /usr/local/tomcat/conf/
COPY ./configs/web_context.xml /usr/local/tomcat/webapps/manager/META-INF/context.xml

# The image has neither wget nor curl, so use bash's /dev/tcp.  $MINE comes from
# docker-compose.yml.  Timing (start_period etc.) is set in docker-compose.yml.
HEALTHCHECK CMD bash -c \
  'exec 3<>/dev/tcp/127.0.0.1/8080 && printf "GET /$MINE/service/version HTTP/1.0\r\nHost: localhost\r\n\r\n" >&3 && head -n 1 <&3 | grep -q " 200 "'
