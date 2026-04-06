FROM node:18-slim

# Install Python 3 and pip
RUN apt-get update || : && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

# Python dependencies
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

# Node.js dependencies
COPY package*.json ./
RUN npm install

# Application Code
COPY server.js .
COPY backtest.py .

EXPOSE 8080

ENV PORT 8080
CMD [ "npm", "start" ]
