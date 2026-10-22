FROM nginx:1.27.1-alpine

# The site config is a template: the image's entrypoint renders
# /etc/nginx/templates/*.template into /etc/nginx/conf.d/ with envsubst at
# start-up.  public-url.envsh (sourced first) derives PUBLIC_HOST/SCHEME/PORT
# from PUBLIC_URL; the filter limits substitution to our own variables so that
# nginx's $host, $uri ... are untouched.
COPY ./nginx.conf.template /etc/nginx/templates/default.conf.template
COPY ./public-url.envsh /docker-entrypoint.d/05-public-url.envsh
ENV NGINX_ENVSUBST_FILTER="^(MINE|PUBLIC_HOST|PUBLIC_SCHEME|PUBLIC_PORT)$"

HEALTHCHECK CMD \
  wget -qO - localhost || exit 1
