// Savora frontend: talks only to POST /recommendations.
// Everything that comes back from the API (LLM text included) is inserted with
// textContent / text nodes, never innerHTML.
(function () {
  "use strict";

  const API_URL = (window.SAVORA_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const REQUEST_TIMEOUT_MS = 30000;
  const VISIBLE_CUISINES = 5;
  const BUDGETS = [["Any", null], ["Low", "low"], ["Medium", "medium"], ["High", "high"]];
  const SVG_NS = "http://www.w3.org/2000/svg";
  const STAR_PATH = "M10 1.5l2.6 5.3 5.9.8-4.3 4.1 1 5.8L10 14.7l-5.2 2.8 1-5.8L1.5 7.6l5.9-.8L10 1.5z";

  const options = window.SAVORA_OPTIONS;
  const $ = (id) => document.getElementById(id);

  const form = $("search-form");
  const locationInput = $("location");
  const locationError = $("location-error");
  const ratingInput = $("min-rating");
  const ratingValue = $("min-rating-value");
  const preferencesInput = $("preferences");
  const submitBtn = $("submit-btn");
  const results = $("results");

  let budget = null;
  let loading = false;
  let expandedCuisines = false;
  const selectedCuisines = new Set();

  class ApiError extends Error {}

  // ---------- DOM helpers ----------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function svg(size, viewBox, shapes) {
    const root = document.createElementNS(SVG_NS, "svg");
    root.setAttribute("width", size);
    root.setAttribute("height", size);
    root.setAttribute("viewBox", viewBox);
    root.setAttribute("fill", "none");
    root.setAttribute("aria-hidden", "true");
    for (const [tag, attrs] of shapes) {
      const shape = document.createElementNS(SVG_NS, tag);
      for (const [name, value] of Object.entries(attrs)) shape.setAttribute(name, value);
      root.appendChild(shape);
    }
    return root;
  }

  // ---------- Filter form ----------

  options.locations.forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    $("location-options").appendChild(option);
  });

  const budgetButtons = BUDGETS.map(([label, value]) => {
    const button = el("button", "segment", label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(value === budget));
    button.addEventListener("click", () => {
      budget = value;
      budgetButtons.forEach((other, i) =>
        other.setAttribute("aria-pressed", String(BUDGETS[i][1] === budget)));
    });
    $("budget-group").appendChild(button);
    return button;
  });

  const moreButton = el("button", "chip chip-more");
  moreButton.type = "button";

  const chipButtons = options.cuisines.map((name) => {
    const button = el("button", "chip", name);
    button.type = "button";
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => {
      if (selectedCuisines.has(name)) selectedCuisines.delete(name);
      else selectedCuisines.add(name);
      button.setAttribute("aria-pressed", String(selectedCuisines.has(name)));
      syncChips();
    });
    $("cuisine-chips").appendChild(button);
    return button;
  });
  $("cuisine-chips").appendChild(moreButton);

  // Collapsed view shows the first few cuisines plus anything already selected,
  // so a selection is never hidden behind "+N more".
  function syncChips() {
    chipButtons.forEach((button, i) => {
      button.hidden = !(expandedCuisines || i < VISIBLE_CUISINES || selectedCuisines.has(options.cuisines[i]));
    });
    const hiddenCount = chipButtons.filter((button) => button.hidden).length;
    moreButton.hidden = !expandedCuisines && hiddenCount === 0;
    moreButton.textContent = expandedCuisines ? "Show less" : "+" + hiddenCount + " more";
  }

  moreButton.addEventListener("click", () => {
    expandedCuisines = !expandedCuisines;
    syncChips();
  });
  syncChips();

  function syncRating() {
    const value = Number(ratingInput.value);
    ratingValue.textContent = value.toFixed(1);
    ratingInput.style.setProperty("--pct", (value / 5) * 100 + "%");
  }
  ratingInput.addEventListener("input", syncRating);
  syncRating();

  function setLocationError(visible) {
    locationError.hidden = !visible;
    locationInput.classList.toggle("is-invalid", visible);
    locationInput.setAttribute("aria-invalid", String(visible));
  }
  locationInput.addEventListener("input", () => setLocationError(false));

  // ---------- Rendering ----------

  const BANNER_ICONS = {
    ai: () => svg(17, "0 0 24 24", [
      ["path", { d: "M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z", fill: "#6EE7B7" }],
    ]),
    empty: () => svg(17, "0 0 24 24", [
      ["circle", { cx: 11, cy: 11, r: 7, stroke: "#7DD3FC", "stroke-width": 2 }],
      ["path", { d: "M20 20l-3.2-3.2", stroke: "#7DD3FC", "stroke-width": 2, "stroke-linecap": "round" }],
    ]),
    fallback: () => svg(17, "0 0 24 24", [
      ["path", { d: "M12 3l9 16H3L12 3z", stroke: "#FCD34D", "stroke-width": 2, "stroke-linejoin": "round" }],
      ["path", { d: "M12 9v4", stroke: "#FCD34D", "stroke-width": 2, "stroke-linecap": "round" }],
      ["circle", { cx: 12, cy: 16.2, r: 0.9, fill: "#FCD34D" }],
    ]),
    error: () => svg(17, "0 0 24 24", [
      ["circle", { cx: 12, cy: 12, r: 9, stroke: "#FDA4AF", "stroke-width": 2 }],
      ["path", { d: "M12 7.5v5.5", stroke: "#FDA4AF", "stroke-width": 2, "stroke-linecap": "round" }],
      ["circle", { cx: 12, cy: 16.2, r: 1, fill: "#FDA4AF" }],
    ]),
  };

  function renderBanner(kind, lead, message) {
    const banner = el("div", "banner banner-" + kind);
    banner.setAttribute("role", kind === "error" ? "alert" : "status");
    const text = el("div");
    if (lead) text.append(el("strong", null, lead), " ");
    text.append(message);
    banner.append(BANNER_ICONS[kind](), text);
    return banner;
  }

  function renderStars(rating) {
    const filled = Math.max(0, Math.min(5, Math.round(rating)));
    const wrap = el("div", "rating");
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", "Rated " + rating + " out of 5");
    for (let i = 0; i < 5; i++) {
      wrap.append(svg(14, "0 0 20 20", [
        ["path", { d: STAR_PATH, class: i < filled ? "star-on" : "star-off" }],
      ]));
    }
    wrap.append(el("span", "rating-number", Number(rating).toFixed(1)));
    return wrap;
  }

  function renderCard(item, isFallback, index) {
    const card = el("article", "card" + (isFallback ? " card-fallback" : ""));

    const title = el("div", "card-title");
    title.append(el("div", "rank", String(index + 1)), el("h3", "card-name", item.name));
    const head = el("div", "card-head");
    head.append(title);
    if (item.estimated_cost) head.append(el("div", "cost", item.estimated_cost));

    const tags = el("div", "tags");
    String(item.cuisine || "")
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean)
      .forEach((name) => tags.append(el("span", "tag", name)));

    card.append(head, tags, renderStars(item.rating), el("p", "explanation", item.explanation));
    return card;
  }

  function renderSkeletons() {
    const grid = el("div", "grid");
    for (let i = 0; i < 4; i++) {
      const card = el("div", "skeleton");
      [55, 35, 30, 100, 80].forEach((width) => {
        const line = el("div", "skeleton-line");
        line.style.width = width + "%";
        card.append(line);
      });
      grid.append(card);
    }
    const status = el("span", "visually-hidden", "Finding the best restaurants for you…");
    results.replaceChildren(status, grid);
  }

  function renderResults(data) {
    if (!Array.isArray(data.recommendations)) {
      throw new ApiError("Unexpected response from the recommendation service.");
    }

    if (data.recommendations.length === 0) {
      results.replaceChildren(renderBanner("empty", null, data.summary || "No restaurants matched your filters. Try relaxing them."));
      return;
    }

    const isAi = data.source === "ai";
    const banner = isAi
      ? renderBanner("ai", "AI-recommended.", data.summary || "")
      : renderBanner("fallback", "AI ranking unavailable.", "Showing restaurants sorted by rating instead. " + (data.summary || ""));

    const grid = el("div", "grid");
    data.recommendations.forEach((item, index) => grid.append(renderCard(item, !isAi, index)));
    results.replaceChildren(banner, grid);
  }

  function renderError(message) {
    results.replaceChildren(renderBanner("error", null, message));
  }

  // ---------- Request ----------

  async function describeHttpError(response) {
    if (response.status === 422) {
      try {
        const body = await response.json();
        const first = Array.isArray(body.detail) && body.detail[0];
        if (first && first.msg) return "Please check your search: " + first.msg.replace(/^Value error, /, "") + ".";
      } catch (_) { /* fall through to the generic message */ }
    }
    return "The recommendation service returned an error (HTTP " + response.status + "). Please try again.";
  }

  function setLoading(isLoading) {
    loading = isLoading;
    submitBtn.disabled = isLoading;
    submitBtn.textContent = isLoading ? "Finding restaurants…" : "Find restaurants";
    results.setAttribute("aria-busy", String(isLoading));
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (loading) return;

    const location = locationInput.value.trim();
    if (!location) {
      setLocationError(true);
      locationInput.focus();
      return;
    }

    const payload = {
      location,
      budget,
      cuisine: Array.from(selectedCuisines),
      min_rating: Number(ratingInput.value),
      preferences: preferencesInput.value.split(",").map((p) => p.trim()).filter(Boolean),
    };

    setLoading(true);
    renderSkeletons();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    try {
      const response = await fetch(API_URL + "/recommendations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!response.ok) throw new ApiError(await describeHttpError(response));
      renderResults(await response.json());
    } catch (err) {
      if (err instanceof ApiError) renderError(err.message);
      else if (err.name === "AbortError") renderError("The request took too long. Please try again.");
      else renderError("Could not reach the recommendation service. Check that it is running and try again.");
    } finally {
      clearTimeout(timer);
      setLoading(false);
    }
  });
})();
