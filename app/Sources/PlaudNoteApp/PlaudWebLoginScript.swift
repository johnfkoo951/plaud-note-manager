import Foundation

let plaudAuthCaptureScript = """
(() => {
  if (window.__plaudAuthCaptureInstalled) { return; }
  window.__plaudAuthCaptureInstalled = true;
  const targetHost = "api-apne1.plaud.ai";
  const post = (url, headers) => {
    try {
      const rawURL = String(url || "");
      if (!rawURL.includes(targetHost)) { return; }
      window.webkit.messageHandlers.plaudAuthCapture.postMessage({
        url: rawURL,
        headers: headers || {}
      });
    } catch (_) {}
  };
  const headersObject = (headers) => {
    const out = {};
    if (!headers) { return out; }
    try {
      if (headers instanceof Headers) {
        headers.forEach((value, key) => { out[String(key).toLowerCase()] = String(value); });
        return out;
      }
      if (Array.isArray(headers)) {
        headers.forEach((pair) => {
          if (pair && pair.length >= 2) { out[String(pair[0]).toLowerCase()] = String(pair[1]); }
        });
        return out;
      }
      Object.keys(headers).forEach((key) => { out[String(key).toLowerCase()] = String(headers[key]); });
    } catch (_) {}
    return out;
  };
  const originalFetch = window.fetch;
  window.fetch = function(input, init) {
    const inputHeaders = input && input.headers ? headersObject(input.headers) : {};
    const initHeaders = init && init.headers ? headersObject(init.headers) : {};
    const url = typeof input === "string" ? input : input && input.url;
    post(url, Object.assign({}, inputHeaders, initHeaders));
    return originalFetch.apply(this, arguments);
  };
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__plaudAuthURL = url;
    this.__plaudAuthHeaders = {};
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(key, value) {
    this.__plaudAuthHeaders[String(key).toLowerCase()] = String(value);
    return originalSetRequestHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function() {
    post(this.__plaudAuthURL, this.__plaudAuthHeaders);
    return originalSend.apply(this, arguments);
  };
})();
"""
