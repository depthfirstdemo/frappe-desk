const assert = require("node:assert/strict");
const Module = require("node:module");
const { test } = require("node:test");

const requests = [];
const superagent = {
	get(url) {
		const req = {
			url,
			headers: {},
			set(name, value) {
				this.headers[name] = value;
				return this;
			},
			query() {
				return this;
			},
			type() {
				return this;
			},
			then(resolve, reject) {
				// Simulate Frappe routing by X-Frappe-Site-Name (or Origin if absent).
				const site = this.headers["X-Frappe-Site-Name"] || "siteA";
				const response =
					this.headers.Authorization === `token ${site}`
						? Promise.resolve({ body: { message: { user: site, user_type: "System User" } } })
						: Promise.reject(new Error("Invalid credentials for site"));
				return response.then(resolve, reject);
			},
		};
		requests.push(req);
		return req;
	},
};

// Stub only the HTTP dependencies while loading the middleware under test.
const load = Module._load;
Module._load = function (name, parent, isMain) {
	if (name === "superagent") return superagent;
	if (name === "cookie") return { parse: () => ({}) };
	return load.call(this, name, parent, isMain);
};
let authenticate;
let frappe_request;
try {
	authenticate = require("../middlewares/authenticate");
	({ frappe_request } = require("../utils"));
} finally {
	Module._load = load;
}

function connect(namespace, authorization, siteHeader) {
	const socket = {
		nsp: { name: namespace },
		request: {
			headers: {
				origin: "https://siteA",
				host: "siteA:9000",
				authorization,
				"x-frappe-site-name": siteHeader,
			},
		},
	};
	return new Promise((resolve) => authenticate(socket, (error) => resolve({ socket, error })));
}

test("empty namespaces are rejected without authenticating", async () => {
	requests.length = 0;
	const { error } = await connect("/", "token siteA", "siteA");
	assert.match(error.message, /Invalid namespace/);
	assert.equal(requests.length, 0);
});

test("siteA credentials cannot authenticate to siteB even with a siteB handshake header", async () => {
	requests.length = 0;
	const { error } = await connect("/siteB", "token siteA", "siteB");
	assert.match(error.message, /Unauthorized/);
	assert.equal(requests.length, 1);
	assert.equal(requests[0].url, "https://siteA/api/method/frappe.realtime.get_user_info");
	assert.equal(requests[0].headers["X-Frappe-Site-Name"], "siteB");
});

test("authentication and permission checks use the namespace, not the handshake site header", async () => {
	requests.length = 0;
	const { socket, error } = await connect("/siteB", "token siteB", "siteA");
	assert.equal(error, undefined);
	assert.equal(socket.site_name, "siteB");
	assert.equal(socket.user, "siteB");

	frappe_request("/api/method/frappe.realtime.can_subscribe_doc", socket);
	assert.equal(requests.length, 2);
	assert.equal(requests[1].headers["X-Frappe-Site-Name"], "siteB");
	assert.equal(requests[1].headers.Authorization, "token siteB");
});
