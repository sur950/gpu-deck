/* GPU Deck website. Vanilla JS, no dependencies, no trackers. Runs on every page (home, docs, 404).
   Theme toggle, mobile menu, the star banner, the star count from the public GitHub API (cached,
   with a quiet fallback), the documentation search (prebuilt JSON index, loaded on first use),
   and the home page's tabs, copy buttons, screenshot lightbox and scroll reveals. */
(function () {
  "use strict";

  var doc = document;
  var root = doc.documentElement;
  var REPO = "sur950/gpu-deck";
  var THEME_KEY = "gpudeck-theme";
  var BANNER_KEY = "gpudeck-star-banner";
  var BASE = siteBase(doc.body.getAttribute("data-root"));
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  var systemLight = window.matchMedia("(prefers-color-scheme: light)");
  var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

  // The site generator writes data-root as "", "../", "../../" ... or, on the 404 page, a
  // root-relative path such as "/gpu-deck/". BASE is rebuilt from those pieces only, so a
  // scheme ("javascript:"), a host ("//evil") or markup in the attribute can never reach a
  // navigation or an href.
  function siteBase(raw) {
    var value = String(raw || "");
    if (/^(?:\.\.\/)+$/.test(value)) return "../".repeat(value.length / 3);
    if (!/^\/(?:[A-Za-z0-9._~-]+\/)*$/.test(value)) return "";
    var parts = value.split("/").filter(Boolean);
    return parts.reduce(function (acc, part) {
      return /^[A-Za-z0-9_~-][A-Za-z0-9._~-]*$/.test(part) ? acc + encodeURIComponent(part) + "/" : acc;
    }, "/");
  }

  function $(sel, ctx) {
    return (ctx || doc).querySelector(sel);
  }
  function $$(sel, ctx) {
    return Array.prototype.slice.call((ctx || doc).querySelectorAll(sel));
  }
  function announce(text) {
    var el = $("[data-announce]");
    if (!el) return;
    el.textContent = "";
    window.setTimeout(function () {
      el.textContent = text;
    }, 30);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  window.GPUDeck = { announce: announce, $: $, $$: $$ };

  // In-page jumps scroll smoothly; the jump to a #fragment on arrival stays instant.
  function smooth() {
    window.setTimeout(function () {
      root.classList.add("smooth-scroll");
    }, 60);
  }
  if (doc.readyState === "complete") smooth();
  else window.addEventListener("load", smooth);

  /* ---------------------------------------------------------------- theme */
  function effectiveTheme() {
    var t = root.getAttribute("data-theme");
    if (t === "light" || t === "dark") return t;
    return systemLight.matches ? "light" : "dark";
  }

  // <picture data-themed>: each <source data-scheme="light|dark"> follows the chosen theme, not only
  // the OS (the home page's screenshots, and the light / dark diagrams in the docs).
  function syncPictures() {
    var forced = root.getAttribute("data-theme");
    $$("picture[data-themed] source[data-scheme]").forEach(function (s) {
      var scheme = s.getAttribute("data-scheme");
      var media = forced ? (forced === scheme ? "all" : "not all") : "(prefers-color-scheme: " + scheme + ")";
      if (s.getAttribute("media") !== media) s.setAttribute("media", media);
    });
  }

  function labelToggle() {
    var next = effectiveTheme() === "dark" ? "light" : "dark";
    $$(".theme-toggle").forEach(function (btn) {
      btn.setAttribute("aria-label", "Switch to " + next + " theme");
    });
    if (root.getAttribute("data-theme")) {
      $$("meta[name='theme-color']").forEach(function (m) {
        m.setAttribute("content", effectiveTheme() === "dark" ? "#0E0F12" : "#F4F3F0");
      });
    }
  }

  function setTheme(theme) {
    var system = systemLight.matches ? "light" : "dark";
    try {
      if (theme === system) localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, theme);
    } catch (e) {
      /* storage blocked: the choice lasts for this page view */
    }
    if (theme === system) root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    syncPictures();
    labelToggle();
  }

  syncPictures();
  labelToggle();
  $$(".theme-toggle").forEach(function (toggle) {
    toggle.addEventListener("click", function () {
      setTheme(effectiveTheme() === "dark" ? "light" : "dark");
      announce(effectiveTheme() === "dark" ? "Dark theme" : "Light theme");
    });
  });
  if (systemLight.addEventListener) {
    systemLight.addEventListener("change", function () {
      syncPictures();
      labelToggle();
    });
  }

  /* ---------------------------------------------------------------- header + menu */
  var header = $(".site-header");
  var nav = $("#site-nav");
  var menuBtn = $(".menu-toggle");

  var firstSection = doc.getElementById("why");
  function onScroll() {
    if (header) header.classList.toggle("scrolled", window.scrollY > 8);
    // Above the first section nothing in the nav is "current".
    if (firstSection && firstSection.getBoundingClientRect().top > window.innerHeight * 0.5) {
      $$(".nav a[aria-current='true']").forEach(function (a) {
        a.removeAttribute("aria-current");
      });
    }
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  function closeMenu(focusButton) {
    if (!nav || !menuBtn) return;
    nav.classList.remove("open");
    menuBtn.setAttribute("aria-expanded", "false");
    menuBtn.setAttribute("aria-label", "Open menu");
    $("use", menuBtn).setAttribute("href", "#i-menu");
    if (focusButton) menuBtn.focus();
  }
  if (menuBtn && nav) {
    menuBtn.addEventListener("click", function () {
      var open = !nav.classList.contains("open");
      nav.classList.toggle("open", open);
      menuBtn.setAttribute("aria-expanded", String(open));
      menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      $("use", menuBtn).setAttribute("href", open ? "#i-close" : "#i-menu");
      if (open) {
        var first = $("a", nav);
        if (first) first.focus();
      }
    });
    nav.addEventListener("click", function (e) {
      if (e.target.closest("a")) closeMenu(false);
    });
    doc.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.classList.contains("open")) closeMenu(true);
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 960 && nav.classList.contains("open")) closeMenu(false);
    });
  }

  // Highlight the nav entry of the home page section in view.
  var navLinks = $$(".nav a[href^='#']");
  if ("IntersectionObserver" in window && navLinks.length) {
    var byId = {};
    navLinks.forEach(function (a) {
      byId[a.getAttribute("href").slice(1)] = a;
    });
    var spy = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          var link = byId[entry.target.id];
          if (!link || !entry.isIntersecting) return;
          navLinks.forEach(function (a) {
            a.removeAttribute("aria-current");
          });
          link.setAttribute("aria-current", "true");
        });
      },
      { rootMargin: "-45% 0px -50% 0px" }
    );
    Object.keys(byId).forEach(function (id) {
      var el = doc.getElementById(id);
      if (el) spy.observe(el);
    });
  }

  /* ---------------------------------------------------------------- star banner */
  var banner = $("[data-banner]");
  var bannerClose = $("[data-banner-close]");
  if (banner && bannerClose) {
    bannerClose.addEventListener("click", function () {
      try {
        localStorage.setItem(BANNER_KEY, "dismissed");
      } catch (e) {
        /* not remembered, but still closed for this page */
      }
      var done = function () {
        root.classList.add("banner-off");
        banner.classList.remove("closing");
      };
      if (reduceMotion.matches) done();
      else {
        banner.style.height = banner.offsetHeight + "px";
        void banner.offsetHeight; // commit the start height before the transition
        banner.classList.add("closing");
        banner.style.height = "";
        window.setTimeout(done, 300);
      }
      var brand = $(".brand");
      if (brand) brand.focus();
      announce("Banner dismissed");
    });
  }

  /* ---------------------------------------------------------------- keyboard hints */
  if (!isMac) {
    $$("[data-kbd-mod]").forEach(function (k) {
      k.textContent = "Ctrl K";
    });
  }

  /* ---------------------------------------------------------------- tabs */
  // Each [data-tabs] container holds one role=tablist; panels are found by aria-controls.
  $$("[data-tabs]").forEach(function (box) {
    var list = $("[role='tablist']", box);
    if (!list) return;
    var tabs = $$("[role='tab']", list);

    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        var panel = doc.getElementById(t.getAttribute("aria-controls"));
        if (panel) panel.hidden = !on;
      });
      if (focus) tab.focus();
    }

    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () {
        select(tab, false);
      });
      tab.addEventListener("keydown", function (e) {
        var next = null;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") next = tabs[(i + 1) % tabs.length];
        else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (e.key === "Home") next = tabs[0];
        else if (e.key === "End") next = tabs[tabs.length - 1];
        if (next) {
          e.preventDefault();
          select(next, true);
        }
      });
    });
  });

  /* ---------------------------------------------------------------- copy */
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = doc.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.className = "sr-only";
      doc.body.appendChild(ta);
      ta.select();
      try {
        doc.execCommand("copy") ? resolve() : reject(new Error("copy failed"));
      } catch (err) {
        reject(err);
      } finally {
        doc.body.removeChild(ta);
      }
    });
  }
  window.GPUDeck.copyText = copyText;

  $$("[data-copy-active]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var card = btn.closest(".code-card");
      if (!card) return;
      // The visible panel of this card (tabbed cards hide the others).
      var pre = $$("pre", card).filter(function (p) {
        return !p.closest("[hidden]");
      })[0];
      if (!pre) return;
      var text = pre.innerText.replace(/\u00a0/g, " ").replace(/\s+$/, "") + "\n";
      copyText(text).then(
        function () {
          btn.classList.add("done");
          $("span", btn).textContent = "Copied";
          announce("Copied to the clipboard");
          window.setTimeout(function () {
            btn.classList.remove("done");
            $("span", btn).textContent = "Copy";
          }, 1800);
        },
        function () {
          announce("Copy failed. Select the text and copy it by hand.");
        }
      );
    });
  });

  /* ---------------------------------------------------------------- gallery + lightbox */
  var moreBtn = $("[data-gallery-more]");
  if (moreBtn) {
    moreBtn.addEventListener("click", function () {
      var hidden = $$("[data-gallery] [data-more]");
      var open = moreBtn.getAttribute("aria-expanded") !== "true";
      hidden.forEach(function (li) {
        li.hidden = !open;
      });
      moreBtn.setAttribute("aria-expanded", String(open));
      moreBtn.textContent = open ? "Show fewer screens" : "Show all 27 screens";
      if (open && hidden[0]) $("button", hidden[0]).focus();
    });
  }

  var dialog = $(".lightbox");
  var items = $$("[data-gallery] button[data-shot]").map(function (b) {
    var img = $("img", b);
    var cap = $(".g-cap", b);
    return {
      shot: b.getAttribute("data-shot"),
      alt: img ? img.getAttribute("alt") : "",
      w: img ? img.getAttribute("width") : "1440",
      h: img ? img.getAttribute("height") : "900",
      cap: cap ? cap.innerHTML : ""
    };
  });
  var current = 0;
  var opener = null;

  // Full-size view: the vector SVG (sharp at any zoom). The name is one encoded path segment.
  function full(shot) {
    return "assets/screenshots/" + encodeURIComponent(String(shot || "")) + ".svg";
  }

  function show(index) {
    if (!dialog || !items.length) return;
    current = (index + items.length) % items.length;
    var it = items[current];
    var img = $(".lb-stage img", dialog);
    img.setAttribute("width", it.w);
    img.setAttribute("height", it.h);
    img.alt = it.alt;
    img.src = full(it.shot);
    $(".lb-cap", dialog).innerHTML = it.cap;
    $(".lb-count", dialog).textContent = current + 1 + " / " + items.length;
    // Warm the neighbours so arrow keys feel instant.
    [current + 1, current - 1].forEach(function (n) {
      var pre = new Image();
      pre.src = full(items[(n + items.length) % items.length].shot);
    });
  }

  function openShot(shot, trigger) {
    if (!dialog || typeof dialog.showModal !== "function") {
      window.open(full(shot), "_blank", "noopener");
      return;
    }
    var idx = 0;
    items.forEach(function (it, i) {
      if (it.shot === shot) idx = i;
    });
    opener = trigger;
    show(idx);
    dialog.showModal();
    $("[data-lb-close]", dialog).focus();
  }

  if (dialog) {
    doc.addEventListener("click", function (e) {
      var trigger = e.target.closest("[data-shot]");
      if (trigger && !trigger.closest(".lightbox")) {
        e.preventDefault();
        openShot(trigger.getAttribute("data-shot"), trigger);
      }
    });
    $("[data-lb-prev]", dialog).addEventListener("click", function () {
      show(current - 1);
    });
    $("[data-lb-next]", dialog).addEventListener("click", function () {
      show(current + 1);
    });
    $("[data-lb-close]", dialog).addEventListener("click", function () {
      dialog.close();
    });
    dialog.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") {
        e.preventDefault();
        show(current + 1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        show(current - 1);
      }
    });
    // A click on the backdrop (outside the dialog box) closes it.
    dialog.addEventListener("click", function (e) {
      if (e.target !== dialog) return;
      var r = dialog.getBoundingClientRect();
      var inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      if (!inside) dialog.close();
    });
    dialog.addEventListener("close", function () {
      if (opener && doc.contains(opener)) opener.focus();
      opener = null;
    });
  }

  /* ---------------------------------------------------------------- reveal on scroll */
  var reveals = $$(".reveal");
  if (!reduceMotion.matches && "IntersectionObserver" in window) {
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            io.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.06 }
    );
    reveals.forEach(function (el) {
      io.observe(el);
    });
    // Anything jumped to through a #hash shows at once.
    window.addEventListener("hashchange", function () {
      reveals.forEach(function (el) {
        el.classList.add("in");
      });
    });
  } else {
    reveals.forEach(function (el) {
      el.classList.add("in");
    });
  }

  /* ---------------------------------------------------------------- search */
  var search = $("[data-search]");
  var input = search && $("[data-search-input]", search);
  var results = search && $("[data-search-results]", search);
  var state = search && $("[data-search-state]", search);
  var hint = state ? state.textContent : "";
  var index = null;
  var loading = null;
  var active = -1;
  var searchOpener = null;

  function loadIndex() {
    if (index || loading) return loading;
    loading = fetch(BASE + "docs/search-index.json", { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        index = [];
        data.docs.forEach(function (d) {
          d.s.forEach(function (s, i) {
            index.push({
              doc: d,
              intro: i === 0 && !s[1],
              h: s[0],
              a: s[1],
              x: s[2],
              hl: s[0].toLowerCase(),
              tl: (d.t + " " + d.l).toLowerCase(),
              xl: s[2].toLowerCase()
            });
          });
        });
        if (input && input.value.trim()) run(input.value);
      })
      .catch(function () {
        loading = null;
        if (state) state.textContent = "The search index could not be loaded. Browse the documentation instead.";
      });
    return loading;
  }

  function count(hay, needle) {
    var n = 0;
    var i = hay.indexOf(needle);
    while (i >= 0 && n < 5) {
      n++;
      i = hay.indexOf(needle, i + needle.length);
    }
    return n;
  }

  function mark(text, terms) {
    var out = escapeHtml(text);
    var safe = terms
      .slice()
      .sort(function (a, b) {
        return b.length - a.length;
      })
      .map(function (t) {
        return escapeHtml(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      });
    if (!safe.length) return out;
    return out.replace(new RegExp("(" + safe.join("|") + ")", "gi"), "<mark>$1</mark>");
  }

  function snippet(entry, terms) {
    var pos = -1;
    terms.forEach(function (t) {
      var p = entry.xl.indexOf(t);
      if (p >= 0 && (pos < 0 || p < pos)) pos = p;
    });
    var text = entry.x;
    if (pos < 0) return text.slice(0, 160) + (text.length > 160 ? "…" : "");
    var start = Math.max(0, pos - 50);
    if (start > 0) {
      var space = text.indexOf(" ", start);
      if (space > 0 && space < pos) start = space + 1;
    }
    var end = Math.min(text.length, start + 180);
    return (start > 0 ? "…" : "") + text.slice(start, end) + (end < text.length ? "…" : "");
  }

  function run(query) {
    var q = query.trim().toLowerCase();
    var terms = q.split(/\s+/).filter(Boolean).slice(0, 8);
    active = -1;
    input.removeAttribute("aria-activedescendant");
    if (!terms.length) {
      results.innerHTML = "";
      input.setAttribute("aria-expanded", "false");
      state.hidden = false;
      state.textContent = hint;
      return;
    }
    if (!index) {
      state.hidden = false;
      state.textContent = "Loading the index…";
      loadIndex();
      return;
    }
    var scored = [];
    index.forEach(function (e) {
      var score = 0;
      for (var i = 0; i < terms.length; i++) {
        var t = terms[i];
        var inHead = e.hl.indexOf(t);
        var inTitle = e.tl.indexOf(t) >= 0;
        var inText = count(e.xl, t);
        if (inHead < 0 && !inTitle && !inText) return;
        if (inHead >= 0) score += 12 + (inHead === 0 || e.hl.charAt(inHead - 1) === " " ? 4 : 0);
        if (inTitle) score += e.intro ? 9 : 4;
        score += inText ? 2 + inText : 0;
      }
      if (terms.length > 1) {
        if (e.hl.indexOf(q) >= 0) score += 14;
        else if (e.xl.indexOf(q) >= 0) score += 6;
      }
      if (e.hl === q) score += 10;
      scored.push([score, e]);
    });
    scored.sort(function (a, b) {
      return b[0] - a[0];
    });
    var perDoc = {};
    var picked = [];
    for (var k = 0; k < scored.length && picked.length < 12; k++) {
      var e = scored[k][1];
      perDoc[e.doc.u] = (perDoc[e.doc.u] || 0) + 1;
      if (perDoc[e.doc.u] <= 4) picked.push(e);
    }
    if (!picked.length) {
      results.innerHTML = "";
      input.setAttribute("aria-expanded", "false");
      state.hidden = false;
      state.textContent = "No results for “" + query.trim() + "”. Try fewer or different words.";
      return;
    }
    state.hidden = true;
    input.setAttribute("aria-expanded", "true");
    results.innerHTML = picked
      .map(function (e, i) {
        var href = BASE + "docs/" + e.doc.u + (e.a ? "#" + e.a : "");
        var title = e.intro
          ? "<small>" + escapeHtml(e.doc.g) + "</small>" + mark(e.doc.l, terms)
          : "<small>" + escapeHtml(e.doc.l) + "</small>" + mark(e.h, terms);
        return (
          '<a class="sr-item" role="option" aria-selected="false" id="sr-' + i + '" href="' + escapeHtml(href) + '">' +
          '<svg class="icon" aria-hidden="true"><use href="#' + (e.intro ? "i-doc" : "i-hash") + '"/></svg>' +
          '<span class="sr-title">' + title + "</span>" +
          '<svg class="icon sr-enter" aria-hidden="true"><use href="#i-enter"/></svg>' +
          '<span class="sr-text">' + mark(snippet(e, terms), terms) + "</span></a>"
        );
      })
      .join("");
    setActive(0, false);
  }

  function setActive(i, scroll) {
    var opts = $$(".sr-item", results);
    if (!opts.length) return;
    active = (i + opts.length) % opts.length;
    opts.forEach(function (o, n) {
      o.setAttribute("aria-selected", String(n === active));
    });
    input.setAttribute("aria-activedescendant", opts[active].id);
    if (scroll) opts[active].scrollIntoView({ block: "nearest" });
  }

  function openSearch(trigger) {
    if (!search || typeof search.showModal !== "function") {
      window.location.href = BASE + "docs/";
      return;
    }
    if (search.open) return;
    searchOpener = trigger || doc.activeElement;
    search.showModal();
    input.focus();
    input.select();
    loadIndex();
  }

  if (search && input) {
    $$("[data-search-open]").forEach(function (b) {
      b.addEventListener("click", function () {
        openSearch(b);
      });
    });
    doc.addEventListener("keydown", function (e) {
      var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "") || (e.target && e.target.isContentEditable);
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        if (search.open) search.close();
        else openSearch();
      } else if (e.key === "/" && !typing && !search.open) {
        e.preventDefault();
        openSearch();
      }
    });
    input.addEventListener("input", function () {
      run(input.value);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive(active + 1, true);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(active - 1, true);
      } else if (e.key === "Enter") {
        var opts = $$(".sr-item", results);
        if (opts[active]) {
          e.preventDefault();
          opts[active].click();
        }
      }
    });
    results.addEventListener("mousemove", function (e) {
      var item = e.target.closest(".sr-item");
      if (!item) return;
      var opts = $$(".sr-item", results);
      var i = opts.indexOf(item);
      if (i >= 0 && i !== active) setActive(i, false);
    });
    results.addEventListener("click", function (e) {
      var item = e.target.closest(".sr-item");
      if (!item) return;
      // Same page: close first, so the jump to the heading is visible.
      var url = new URL(item.href, window.location.href);
      if (url.pathname === window.location.pathname) {
        searchOpener = null;
        search.close();
      }
    });
    $("[data-search-close]", search).addEventListener("click", function () {
      search.close();
    });
    search.addEventListener("click", function (e) {
      if (e.target === search) search.close();
    });
    search.addEventListener("close", function () {
      if (searchOpener && doc.contains(searchOpener) && searchOpener.focus) searchOpener.focus();
      searchOpener = null;
    });
  }

  /* ---------------------------------------------------------------- GitHub data */
  // Unauthenticated API: 60 requests an hour per IP, so answers are cached for an hour.
  function cached(key, maxAgeMs) {
    try {
      var raw = localStorage.getItem(key);
      if (!raw) return null;
      var obj = JSON.parse(raw);
      if (Date.now() - obj.t > maxAgeMs) return null;
      return obj.v;
    } catch (e) {
      return null;
    }
  }
  function store(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify({ t: Date.now(), v: value }));
    } catch (e) {
      /* ignore */
    }
  }
  function getJSON(url) {
    if (!window.fetch) return Promise.reject(new Error("no fetch"));
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = window.setTimeout(function () {
      if (ctrl) ctrl.abort();
    }, 6000);
    return fetch(url, {
      headers: { Accept: "application/vnd.github+json" },
      signal: ctrl ? ctrl.signal : undefined,
      credentials: "omit",
      referrerPolicy: "no-referrer"
    }).then(function (r) {
      window.clearTimeout(timer);
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function formatCount(n) {
    if (n >= 10000) return Math.round(n / 1000) + "k";
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return String(n);
  }
  function applyStars(n) {
    if (typeof n !== "number" || n < 1) {
      root.classList.add("no-stars");
      return;
    }
    $$("[data-stars-value]").forEach(function (el) {
      el.textContent = formatCount(n);
    });
    $$("[data-stars-label]").forEach(function (el) {
      el.textContent = ", " + n + " stars";
    });
  }
  function applyRelease(tag) {
    if (typeof tag !== "string" || !/^v?\d+\.\d+\.\d+/.test(tag)) return;
    var v = tag.charAt(0) === "v" ? tag : "v" + tag;
    $$("[data-release]").forEach(function (el) {
      el.textContent = v;
    });
    $$("[data-version]").forEach(function (el) {
      el.textContent = v.slice(1);
    });
    $$("[data-release-link]").forEach(function (a) {
      a.href = "https://github.com/" + REPO + "/releases/tag/" + v;
    });
  }

  var HOUR = 3600 * 1000;
  var stars = cached("gpudeck-stars", HOUR);
  if (stars !== null) applyStars(stars);
  else
    getJSON("https://api.github.com/repos/" + REPO).then(
      function (data) {
        store("gpudeck-stars", data.stargazers_count);
        applyStars(data.stargazers_count);
      },
      function () {
        store("gpudeck-stars", 0); // plain buttons, no count, for an hour
        applyStars(0);
      }
    );

  if ($("[data-release]")) {
    var release = cached("gpudeck-release", HOUR);
    if (release !== null) applyRelease(release);
    else
      getJSON("https://api.github.com/repos/" + REPO + "/releases/latest").then(
        function (data) {
          store("gpudeck-release", data.tag_name);
          applyRelease(data.tag_name);
        },
        function () {
          store("gpudeck-release", ""); // keep the built-in version for an hour
        }
      );
  }
})();
