/* Node-RED settings. Editor binds to loopback; the nginx gate is the auth
   boundary, so no adminAuth here. User data lives in userDir on /mnt/data. */
module.exports = {
    uiHost: "127.0.0.1",
    uiPort: 1880,

    httpAdminRoot: "/",

    userDir: "/mnt/data/wb-node-red",
    flowFile: "flows.json",
    flowFilePretty: true,

    logging: {
        console: {
            level: "info",
            metrics: false,
            audit: false
        }
    },

    editorTheme: {
        projects: {
            enabled: false
        }
    }
};

/* Optional user overrides (shallow merge; survives upgrades — this template
   does not). A broken override fails the service loudly. */
try {
    Object.assign(module.exports, require("/mnt/data/wb-node-red/settings-user.js"));
} catch (err) {
    if (err.code !== "MODULE_NOT_FOUND") {
        throw err;
    }
}

// re-asserted after the merge: loopback bind is the security boundary,
// userDir/flowFile are packaging invariants
module.exports.uiHost = "127.0.0.1";
module.exports.uiPort = 1880;
module.exports.httpAdminRoot = "/";
module.exports.userDir = "/mnt/data/wb-node-red";
module.exports.flowFile = "flows.json";
