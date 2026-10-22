FROM nginx:1.27.1-alpine
COPY ./nginx.conf /etc/nginx/conf.d/default.conf

HEALTHCHECK CMD \
  wget -qO - localhost || exit 1
