FROM node:20-alpine

WORKDIR /app

COPY services/web/package.json services/web/package-lock.json* /app/
RUN npm install

COPY services/web /app

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]

