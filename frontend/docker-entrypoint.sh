#!/bin/sh
# Substitute only ${NGINX_BACKEND_URL} — all other nginx $variables are
# left as-is because envsubst receives a restricted substitution list.
envsubst '${NGINX_BACKEND_URL}' \
  < /etc/nginx/conf.d/default.conf.template \
  > /etc/nginx/conf.d/default.conf

exec nginx -g "daemon off;"
