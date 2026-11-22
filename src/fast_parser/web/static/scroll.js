(() => {
  let pageY = 0;
  let facets = {};
  let active = false;

  function isBrowserRequest(event) {
    const target = event.detail && event.detail.target;
    if (target && target.id === "browser") return true;
    const source = event.target;
    return !!(source && source.closest && source.closest("#browser"));
  }

  function rememberFacets() {
    const out = {};
    document.querySelectorAll("#browser .facet[data-facet]").forEach((el) => {
      out[el.getAttribute("data-facet")] = el.scrollTop;
    });
    return out;
  }

  function restoreFacets(saved) {
    document.querySelectorAll("#browser .facet[data-facet]").forEach((el) => {
      const key = el.getAttribute("data-facet");
      if (key in saved) el.scrollTop = saved[key];
    });
  }

  function restorePage() {
    window.scrollTo(0, pageY);
    restoreFacets(facets);
  }

  document.addEventListener("htmx:beforeRequest", (event) => {
    if (!isBrowserRequest(event)) return;
    active = true;
    pageY = window.scrollY;
    facets = rememberFacets();
    if (location.hash) {
      history.replaceState(null, "", location.pathname + location.search);
    }
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    if (!active || !isBrowserRequest(event)) return;
    restorePage();
  });

  document.addEventListener("htmx:afterSettle", (event) => {
    if (!active || !isBrowserRequest(event)) return;
    restorePage();
    requestAnimationFrame(restorePage);
    setTimeout(restorePage, 0);
    active = false;
  });
})();
