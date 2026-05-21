FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --prefer-offline
COPY . .
ARG VITE_ENTRA_TENANT_ID
ARG VITE_ENTRA_CLIENT_ID
ARG VITE_API_URL
ARG VITE_AUTH_DISABLED
ENV VITE_ENTRA_TENANT_ID=$VITE_ENTRA_TENANT_ID
ENV VITE_ENTRA_CLIENT_ID=$VITE_ENTRA_CLIENT_ID
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_AUTH_DISABLED=$VITE_AUTH_DISABLED
RUN npm run build

FROM nginx:1.27-alpine
# Remove the default nginx site config
RUN rm -f /etc/nginx/conf.d/default.conf

# Install gettext for envsubst
RUN apk add --no-cache gettext

# Store nginx config as a template — docker-entrypoint.sh substitutes
# ${NGINX_BACKEND_URL} at container startup before nginx begins serving.
COPY nginx.conf /etc/nginx/conf.d/default.conf.template
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
ENTRYPOINT ["/docker-entrypoint.sh"]
