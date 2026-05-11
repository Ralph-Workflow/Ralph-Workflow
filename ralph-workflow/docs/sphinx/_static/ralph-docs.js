// Ralph Docs — small enhancements layered on top of the static Sphinx output.
// Strict scope: scroll-spy for right TOC + close-mobile-drawer-on-link-click.
// No third-party deps. No framework. Total ~60 lines on purpose.
(function () {
  "use strict";

  // ── 1) Right-TOC scroll-spy ─────────────────────────────────────────────
  function initTocSpy() {
    var tocLinks = Array.prototype.slice.call(
      document.querySelectorAll(".ralph-toc-tree a[href^='#']")
    );
    if (tocLinks.length === 0) return;

    var entries = tocLinks
      .map(function (link) {
        var id = decodeURIComponent(link.getAttribute("href").slice(1));
        if (!id) return null;
        var target = document.getElementById(id);
        return target ? { link: link, target: target } : null;
      })
      .filter(Boolean);
    if (entries.length === 0) return;

    function setActive(activeLink) {
      tocLinks.forEach(function (l) {
        l.classList.toggle("toc-active", l === activeLink);
      });
    }

    if ("IntersectionObserver" in window) {
      var visible = new Map();
      var io = new IntersectionObserver(
        function (records) {
          records.forEach(function (r) {
            if (r.isIntersecting) visible.set(r.target, r);
            else visible.delete(r.target);
          });
          if (visible.size > 0) {
            // Pick the highest visible heading (smallest top)
            var top = null;
            visible.forEach(function (r) {
              if (top === null || r.boundingClientRect.top < top.boundingClientRect.top) top = r;
            });
            var entry = entries.find(function (e) { return e.target === top.target; });
            if (entry) setActive(entry.link);
          }
        },
        { rootMargin: "-80px 0px -65% 0px", threshold: [0, 1] }
      );
      entries.forEach(function (e) { io.observe(e.target); });
    } else {
      // Fallback: passive scroll listener
      var ticking = false;
      function update() {
        ticking = false;
        var top = window.pageYOffset + 100;
        var current = entries[0];
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].target.offsetTop <= top) current = entries[i];
        }
        setActive(current.link);
      }
      window.addEventListener("scroll", function () {
        if (!ticking) { window.requestAnimationFrame(update); ticking = true; }
      }, { passive: true });
      update();
    }
  }

  // ── 2) Close mobile sidebar drawer on link click ────────────────────────
  function initSidebarClose() {
    var toggle = document.getElementById("__navigation");
    if (!toggle) return;
    var sidebar = document.getElementById("ralph-sidebar");
    if (!sidebar) return;
    sidebar.addEventListener("click", function (event) {
      var link = event.target.closest("a[href]");
      if (!link) return;
      // Only close on small screens — sidebar drawer is open
      if (window.matchMedia("(max-width: 64rem)").matches) {
        toggle.checked = false;
      }
    });
    // Esc to close drawer
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && toggle.checked) toggle.checked = false;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initTocSpy();
      initSidebarClose();
    });
  } else {
    initTocSpy();
    initSidebarClose();
  }
})();
