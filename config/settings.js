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
