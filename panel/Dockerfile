# --- 1-bosqich: frontend build ---
# Aniq versiyalar (audit 2026-11-03): suzuvchi teg har build'da jimgina yangilanardi.
FROM node:22.12-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# --- 2-bosqich: Nginx (SPA + reverse proxy) ---
FROM nginx:1.27.5-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
