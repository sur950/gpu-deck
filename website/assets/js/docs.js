/* GPU Deck documentation pages. Vanilla JS, loaded after site.js.
   The sidebar drawer on small screens, the "On this page" scroll-spy, copy buttons on code
   blocks, copy-on-click for heading links, and full-size diagrams. */
(function () {
  "use strict";

  var doc = document;
  var root = doc.documentElement;
  var G = window.GPUDeck || {};
  var $ = function (sel, ctx) {
    return (ctx || doc).querySelector(sel);
  };
  var $$ = function (sel, ctx) {
    return Array.prototype.slice.call((ctx || doc).querySelectorAll(sel));
  };
  var announce = G.announce || function () {};
  var copyText =
    G.copyText ||
    function (t) {
      return navigator.clipboard.writeText(t);
    };

  /* ---------------------------------------------------------------- sidebar drawer */
  var side = $("#docs-nav");
  var barBtn = $(".docs-bar-btn");
  var scrim = null;

  function isDrawer() {
    return window.matchMedia("(max-width: 960px)").matches;
  }
  function closeDrawer(focusButton) {
    if (!root.classList.contains("docs-nav-open")) return;
    root.classList.remove("docs-nav-open");
    if (barBtn) barBtn.setAttribute("aria-expanded", "false");
    if (scrim) {
      var s = scrim;
      scrim = null;
      window.setTimeout(function () {
        s.remove();
      }, 220);
    }
    if (focusButton && barBtn) barBtn.focus();
  }
  function openDrawer() {
    scrim = doc.createElement("div");
    scrim.className = "docs-scrim";
    scrim.addEventListener("click", function () {
      closeDrawer(true);
    });
    doc.body.appendChild(scrim);
    void scrim.offsetWidth;
    root.classList.add("docs-nav-open");
    barBtn.setAttribute("aria-expanded", "true");
    var cur = $("a[aria-current='page']", side) || $("a", side);
    if (cur) cur.focus({ preventScroll: true });
    if (cur) keepVisible(side, cur, true);
  }
  if (side && barBtn) {
    barBtn.addEventListener("click", function () {
      if (root.classList.contains("docs-nav-open")) closeDrawer(true);
      else openDrawer();
    });
    side.addEventListener("click", function (e) {
      if (e.target.closest("a, [data-search-open]") && isDrawer()) closeDrawer(false);
    });
    doc.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeDrawer(true);
      // keep Tab inside the open drawer
      if (e.key === "Tab" && root.classList.contains("docs-nav-open")) {
        var f = $$("a, button", side);
        if (!f.length) return;
        if (e.shiftKey && doc.activeElement === f[0]) {
          e.preventDefault();
          f[f.length - 1].focus();
        } else if (!e.shiftKey && doc.activeElement === f[f.length - 1]) {
          e.preventDefault();
          f[0].focus();
        }
      }
    });
    window.addEventListener("resize", function () {
      if (!isDrawer()) closeDrawer(false);
    });
  }

  function keepVisible(box, el, center) {
    if (!box || !el) return;
    var b = box.getBoundingClientRect();
    var r = el.getBoundingClientRect();
    if (center) {
      box.scrollTop += r.top - b.top - b.height / 2 + r.height / 2;
    } else if (r.top < b.top + 40) {
      box.scrollTop -= b.top + 40 - r.top;
    } else if (r.bottom > b.bottom - 40) {
      box.scrollTop += r.bottom - (b.bottom - 40);
    }
  }
  // The current page's entry starts in view in the desktop sidebar.
  if (side && !isDrawer()) keepVisible(side, $("a[aria-current='page']", side), true);

  /* ---------------------------------------------------------------- on this page */
  var tocBox = $(".docs-toc");
  var tocLinks = $$(".toc-list a[href^='#']");
  var heads = [];
  var seen = {};
  tocLinks.forEach(function (a) {
    var id = decodeURIComponent(a.getAttribute("href").slice(1));
    if (seen[id]) return;
    var h = doc.getElementById(id);
    if (h) {
      seen[id] = true;
      heads.push(h);
    }
  });

  function offset() {
    var bar = $(".docs-bar");
    var barH = bar && getComputedStyle(bar).display !== "none" ? bar.offsetHeight : 0;
    var header = $(".site-header");
    return (header ? header.offsetHeight : 60) + barH + 32;
  }
  var currentId = null;
  function spy() {
    if (!heads.length) return;
    var line = offset();
    var cur = null;
    for (var i = 0; i < heads.length; i++) {
      if (heads[i].getBoundingClientRect().top - line <= 1) cur = heads[i];
      else break;
    }
    var bottom = window.innerHeight + window.scrollY >= doc.documentElement.scrollHeight - 4;
    if (bottom) cur = heads[heads.length - 1];
    var id = cur ? cur.id : null;
    if (id === currentId) return;
    currentId = id;
    tocLinks.forEach(function (a) {
      if (id && a.getAttribute("href") === "#" + id) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
    if (tocBox && id) keepVisible(tocBox, $(".toc-list a[aria-current='true']", tocBox), false);
  }
  var ticking = false;
  window.addEventListener(
    "scroll",
    function () {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(function () {
        ticking = false;
        spy();
      });
    },
    { passive: true }
  );
  window.addEventListener("resize", spy);
  window.addEventListener("load", spy);
  spy();

  // The inline "On this page" closes once a section is picked.
  var inline = $(".toc-inline");
  if (inline) {
    inline.addEventListener("click", function (e) {
      if (e.target.closest("a")) inline.open = false;
    });
  }

  /* ---------------------------------------------------------------- code copy */
  $$("[data-copy-code]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var code = $("pre code", btn.parentNode);
      if (!code) return;
      copyText(code.innerText.replace(/\u00a0/g, " ").replace(/\s+$/, "") + "\n").then(
        function () {
          btn.classList.add("done");
          btn.setAttribute("aria-label", "Copied");
          announce("Copied to the clipboard");
          window.setTimeout(function () {
            btn.classList.remove("done");
            btn.setAttribute("aria-label", "Copy code");
          }, 1600);
        },
        function () {
          announce("Copy failed. Select the text and copy it by hand.");
        }
      );
    });
  });

  /* ---------------------------------------------------------------- heading links */
  $$(".h-anchor").forEach(function (a) {
    a.addEventListener("click", function () {
      var url = window.location.href.split("#")[0] + a.getAttribute("href");
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(url).then(function () {
          announce("Link to this section copied");
        });
      }
    });
  });

  /* ---------------------------------------------------------------- diagrams */
  var dlg = null;
  function diagramDialog() {
    if (dlg) return dlg;
    dlg = doc.createElement("dialog");
    dlg.className = "diagram-dialog";
    dlg.setAttribute("aria-label", "Diagram");
    dlg.innerHTML =
      '<div class="diagram-dialog-bar"><span data-dg-title></span>' +
      '<button type="button" class="icon-btn" data-dg-close aria-label="Close"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></button></div>' +
      '<div class="diagram"><div class="diagram-scroll" data-dg-stage></div></div>';
    doc.body.appendChild(dlg);
    $("[data-dg-close]", dlg).addEventListener("click", function () {
      dlg.close();
    });
    dlg.addEventListener("click", function (e) {
      if (e.target === dlg) dlg.close();
    });
    return dlg;
  }
  function showDiagram(svg, btn) {
    var d = diagramDialog();
    var stage = $("[data-dg-stage]", d);
    stage.innerHTML = "";
    svg.style.width = (svg.getAttribute("data-width") || 1000) + "px";
    stage.appendChild(svg);
    $("[data-dg-title]", d).textContent = svg.getAttribute("aria-label") || "Diagram";
    d.showModal();
    d.addEventListener(
      "close",
      function () {
        stage.innerHTML = "";
        btn.focus();
      },
      { once: true }
    );
  }
  // Screenshots and drawn diagrams are scaled down to the article column: a button on each one
  // opens it full size in the same dialog (the image the current theme shows).
  $$(".prose p > img, .prose p > picture > img").forEach(function (img) {
    var para = img.closest("p");
    if (!para || $$("img", para).length !== 1 || typeof HTMLDialogElement === "undefined") return;
    var btn = doc.createElement("button");
    btn.type = "button";
    btn.className = "diagram-expand img-expand";
    btn.setAttribute("aria-label", "Open the image full size");
    btn.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-expand"/></svg>';
    para.classList.add("has-zoom");
    para.appendChild(btn);
    btn.addEventListener("click", function () {
      var d = diagramDialog();
      var stage = $("[data-dg-stage]", d);
      var big = doc.createElement("img");
      var w = Number(img.getAttribute("width")) || img.naturalWidth || 1000;
      big.src = img.currentSrc || img.src;
      big.alt = img.alt;
      big.className = "dg-img";
      big.style.width = Math.round(img.hasAttribute("data-art") ? w * 1.25 : w) + "px";
      stage.innerHTML = "";
      stage.appendChild(big);
      var title = (img.alt || "Image").split(/[:.]/)[0];
      $("[data-dg-title]", d).textContent = title.length > 90 ? title.slice(0, 89) + "…" : title;
      d.showModal();
      d.addEventListener(
        "close",
        function () {
          stage.innerHTML = "";
          btn.focus();
        },
        { once: true }
      );
    });
  });

  var fullCache = {};
  $$("[data-diagram-expand]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var fig = btn.closest(".diagram");
      var svg = $("svg.dg-svg", fig);
      if (!svg || typeof HTMLDialogElement === "undefined") return;
      var full = fig.getAttribute("data-full");
      if (!full) {
        // The copy keeps the id its scoped styles need; it only lives while the dialog is open.
        showDiagram(svg.cloneNode(true), btn);
        return;
      }
      // The article shows the top-to-bottom variant; the full view is the left-to-right original.
      var label = svg.getAttribute("aria-label");
      (fullCache[full] || (fullCache[full] = fetch(full).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })))
        .then(function (text) {
          var box = doc.createElement("div");
          box.innerHTML = text.replace(/<style>[\s\S]*?<\/style>/, "");
          var big = $("svg", box);
          if (!big) throw new Error("no svg");
          if (label) big.setAttribute("aria-label", label);
          showDiagram(big, btn);
        })
        .catch(function () {
          showDiagram(svg.cloneNode(true), btn);
        });
    });
  });
})();
