FROM node:20-slim

WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev

COPY index.mjs ./

ENV DB_SERVICE_HOST=0.0.0.0
ENV DB_SERVICE_PORT=3000

EXPOSE 3000

CMD ["npm", "start"]
