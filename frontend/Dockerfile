# --- build stage ---
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# --- serve stage ---
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Mounted as a template: nginx's entrypoint runs envsubst on it at startup,
# expanding ${BACKEND_URL} into /etc/nginx/conf.d/default.conf.
COPY nginx.conf /etc/nginx/templates/default.conf.template
# Default backend target for `docker compose`; the Helm deploy overrides this
# with the in-cluster backend Service via app.extraEnv.BACKEND_URL.
ENV BACKEND_URL=http://backend:8000
EXPOSE 80
