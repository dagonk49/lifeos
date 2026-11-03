// Preloaded in the Playwright processes by scripts/test-e2e-stack.sh (NODE_OPTIONS=--require):
// the test domain resolves to Traefik for Node's own requests too (`request` fixture,
// route.fetch), as --host-resolver-rules does for Chromium. Its certificate authority is
// given to Node with NODE_EXTRA_CA_CERTS. Nothing else is changed.
const dns = require("node:dns");

const host = process.env.LIFEOS_E2E_STACK_HOST;
const address = process.env.LIFEOS_E2E_STACK_ADDRESS;

if (host && address) {
  const lookup = dns.lookup;
  dns.lookup = function lookupStackHost(hostname, options, callback) {
    if (hostname !== host) return lookup.call(this, hostname, options, callback);
    const done = typeof options === "function" ? options : callback;
    const all = typeof options === "object" && options !== null && options.all;
    if (all) process.nextTick(done, null, [{ address, family: 4 }]);
    else process.nextTick(done, null, address, 4);
    return undefined;
  };
  // Playwright's own connections ask each family in turn (dns.promises.lookup, all: true).
  const lookupPromise = dns.promises.lookup;
  dns.promises.lookup = async function lookupStackHostPromise(hostname, options) {
    if (hostname !== host) return lookupPromise.call(this, hostname, options);
    const family = typeof options === "object" && options !== null ? options.family : options;
    const found = family === 6 ? [] : [{ address, family: 4 }];
    return typeof options === "object" && options !== null && options.all ? found : { address, family: 4 };
  };
}
