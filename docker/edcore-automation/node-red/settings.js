"use strict";

const fs = require("fs");

function readSecret(path) {
    const value = fs.readFileSync(path, "utf8").trim();
    if (!value) {
        throw new Error(`Required secret file is empty: ${path}`);
    }
    return value;
}

const adminUsername = process.env.NODE_RED_ADMIN_USERNAME || "admin";
if (!/^[A-Za-z0-9_.-]{3,64}$/.test(adminUsername)) {
    throw new Error("NODE_RED_ADMIN_USERNAME has an invalid format");
}

const adminPasswordHash = readSecret("/run/secrets/node_red_admin_password_hash");
if (!/^\$2[aby]\$/.test(adminPasswordHash)) {
    throw new Error("Node-RED administrator password must be a bcrypt hash");
}

module.exports = {
    flowFile: "edcore-automation",
    uiHost: "0.0.0.0",
    uiPort: 1880,
    https: {
        key: fs.readFileSync("/run/secrets/node_red_tls_key"),
        cert: fs.readFileSync("/run/secrets/node_red_tls_cert"),
        ca: fs.readFileSync("/run/secrets/automation_ca_cert"),
        minVersion: "TLSv1.2",
    },

    credentialSecret: readSecret("/run/secrets/node_red_credential_secret"),
    adminAuth: {
        type: "credentials",
        sessionExpiryTime: 28800,
        tokensExpireIn: 28800,
        users: [{
            username: adminUsername,
            password: adminPasswordHash,
            permissions: "*",
        }],
    },
    httpNodeAuth: {
        user: adminUsername,
        pass: adminPasswordHash,
    },
    httpStaticAuth: {
        user: adminUsername,
        pass: adminPasswordHash,
    },
    httpNodeRoot: "/api",
    apiMaxLength: "256kb",

    editorTheme: {
        projects: {
            enabled: true,
            workflow: {mode: "manual"},
        },
        palette: {editable: false},
    },
    externalModules: {
        autoInstall: false,
        autoInstallRetry: 0,
        palette: {allowInstall: false, allowUpload: false, allowList: []},
        modules: {allowInstall: false, allowList: []},
    },
    functionExternalModules: false,
    functionTimeout: 10,
    mqttReconnectTime: 5000,
    socketReconnectTime: 10000,
    debugMaxLength: 4096,

    contextStorage: {
        default: {
            module: "localfilesystem",
            config: {flushInterval: 60},
        },
    },
    runtimeState: {enabled: true, ui: true},
    diagnostics: {enabled: false, ui: false},
    telemetry: {enabled: false},
    exportGlobalContextKeys: false,
    logging: {
        console: {
            level: "info",
            metrics: false,
            audit: true,
        },
    },

    httpAdminMiddleware(req, res, next) {
        res.setHeader("X-Content-Type-Options", "nosniff");
        res.setHeader("X-Frame-Options", "DENY");
        res.setHeader("Referrer-Policy", "no-referrer");
        res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
        res.setHeader("Content-Security-Policy", "frame-ancestors 'none'");
        next();
    },
    httpNodeMiddleware(req, res, next) {
        res.setHeader("X-Content-Type-Options", "nosniff");
        res.setHeader("X-Frame-Options", "DENY");
        res.setHeader("Cache-Control", "no-store");
        next();
    },
};
