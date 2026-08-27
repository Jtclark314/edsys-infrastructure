"use strict";

const fs = require("fs");
const https = require("https");

try {
    if (fs.readFileSync("/data/.edsys-health/mqtt.status", "utf8") !== "connected\n") {
        process.exit(1);
    }
} catch (_) {
    process.exit(1);
}

const request = https.get({
    host: "127.0.0.1",
    port: 1880,
    path: "/",
    servername: "node-red",
    ca: fs.readFileSync("/run/secrets/automation_ca_cert"),
    rejectUnauthorized: true,
    timeout: 4000,
}, (response) => {
    response.resume();
    process.exit(response.statusCode >= 200 && response.statusCode < 500 ? 0 : 1);
});

request.on("timeout", () => request.destroy(new Error("timeout")));
request.on("error", () => process.exit(1));
