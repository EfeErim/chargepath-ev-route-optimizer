"use strict";

const state = {
  config: null,
  origin: null,
  destination: null,
  selectionRole: null,
  map: null,
  tileLayer: null,
  endpointMarkers: [],
  routeLayers: [],
  stopMarkers: [],
  result: null,
  selectedOption: 0,
  inputRevision: 0,
  planController: null,
};

const els = {};
const strategyNames = {
  fastest: "Fastest",
  shortest_distance: "Shortest distance",
  fewest_charging_stops: "Fewest charging stops",
  greedy_fixed_80: "Fixed 80%",
};
const strategyOrder = Object.keys(strategyNames);

document.addEventListener("DOMContentLoaded", () => {
  [
    "modePill",
    "dataLabel",
    "selectionHint",
    "originButton",
    "destinationButton",
    "originValue",
    "destinationValue",
    "originAction",
    "destinationAction",
    "battery",
    "initialSoc",
    "consumption",
    "maxPower",
    "reserveSoc",
    "safety",
    "formError",
    "planButton",
    "planButtonText",
    "mapStatus",
    "mapFallback",
    "basemapWarning",
    "fitButton",
    "resultEmpty",
    "emptyTitle",
    "emptyText",
    "resultLoading",
    "resultFailure",
    "failureText",
    "resultContent",
    "optionTabs",
    "strategyLabel",
    "routeTitle",
    "totalTime",
    "distance",
    "arrivalSoc",
    "driveTime",
    "chargeTime",
    "socTimeline",
    "timelineReserve",
    "chargingPlan",
    "stopCount",
    "unavailableBox",
    "unavailableText",
    "modelBoundary",
  ].forEach((id) => {
    els[id] = document.getElementById(id);
  });

  els.originButton.addEventListener("click", () => chooseRole("origin"));
  els.destinationButton.addEventListener("click", () => chooseRole("destination"));
  els.planButton.addEventListener("click", planRoute);
  els.fitButton.addEventListener("click", fitVisibleRoute);
  document.querySelectorAll(".input-grid input").forEach((input) => {
    input.addEventListener("input", () => {
      resetPlanForChangedInputs();
      validateForm();
    });
  });
  window.addEventListener("resize", () => {
    if (state.map) state.map.invalidateSize({pan: false});
  });
  loadConfig();
});

async function loadConfig() {
  try {
    const response = await fetch("/api/config", {headers: {Accept: "application/json"}});
    if (!response.ok) throw new Error(`Could not load configuration (${response.status})`);
    state.config = await response.json();
    applyConfig();
  } catch (error) {
    showFailure(error instanceof Error ? error.message : "Could not start the application.");
    els.mapFallback.hidden = false;
  }
}

function applyConfig() {
  const config = state.config;
  els.modePill.textContent = config.mode === "fixture" ? "Demo mode" : "Road network";
  els.dataLabel.textContent =
    config.mode === "fixture" ? "Synthetic demo data · not live" : config.data_label;
  els.dataLabel.title = config.data_label;
  const fields = {
    battery: "usable_battery_kwh",
    initialSoc: "initial_soc_pct",
    consumption: "consumption_kwh_per_100km",
    maxPower: "max_dc_power_kw",
    reserveSoc: "reserve_soc_pct",
    safety: "energy_safety_factor",
  };
  Object.entries(fields).forEach(([id, key]) => {
    els[id].value = config.vehicle[key];
    els[id].min = config.input_bounds[key][0];
    els[id].max = config.input_bounds[key][1];
  });
  if (config.mode === "fixture") {
    [state.origin, state.destination] = config.endpoints;
    els.originButton.disabled = true;
    els.destinationButton.disabled = true;
    els.originAction.textContent = "Fixed";
    els.destinationAction.textContent = "Fixed";
    els.selectionHint.textContent = "This demo uses two fixed synthetic endpoints.";
  } else {
    els.originButton.disabled = false;
    els.destinationButton.disabled = false;
    els.originAction.textContent = "Choose";
    els.destinationAction.textContent = "Choose";
    els.selectionHint.textContent = config.integration.coordinates_leave_device
      ? "Choose points A and B. Coordinates are sent to the configured remote OSRM provider."
      : "Choose an origin or destination card, then click the map.";
  }
  updateEndpointLabels();
  initMap();
  validateForm();
}

function initMap() {
  if (!window.L) {
    els.mapFallback.hidden = false;
    els.fitButton.disabled = true;
    setMapStatus("Route explanation available");
    return;
  }
  const points = state.config.endpoints;
  const center = points.length ? [points[0].latitude, points[0].longitude] : [39.8, 32.7];
  state.map = L.map("map", {zoomControl: false, attributionControl: true}).setView(
    center,
    points.length ? 7 : 6,
  );
  L.control.zoom({position: "bottomleft"}).addTo(state.map);
  els.fitButton.disabled = false;
  let tileErrors = 0;
  state.tileLayer = L.tileLayer(state.config.tile_url, {
    attribution: state.config.tile_attribution,
    maxZoom: 19,
    detectRetina: true,
    updateWhenIdle: true,
    keepBuffer: 1,
  })
    .on("tileerror", () => {
      tileErrors += 1;
      if (tileErrors >= 2) els.basemapWarning.hidden = false;
    })
    .addTo(state.map);
  state.map.on("click", (event) => {
    if (!state.selectionRole) return;
    if (state.config.mode === "fixture") {
      els.formError.textContent =
        "Demo mode only supports the two marked synthetic endpoints.";
      return;
    }
    assignPoint(state.selectionRole, {
      id: state.selectionRole,
      longitude: roundCoordinate(event.latlng.lng),
      latitude: roundCoordinate(event.latlng.lat),
    });
  });
  renderEndpointMarkers();
}

function chooseRole(role) {
  if (!state.config || state.config.mode === "fixture") return;
  state.selectionRole = role;
  els.originButton.classList.toggle("is-active", role === "origin");
  els.destinationButton.classList.toggle("is-active", role === "destination");
  els.selectionHint.textContent =
    state.config.mode === "fixture"
      ? `Click marker ${role === "origin" ? "A" : "B"}.`
      : `Click the map to set the ${role === "origin" ? "origin" : "destination"}.`;
}

function assignPoint(role, point) {
  state[role] = point;
  state.selectionRole = null;
  els.originButton.classList.remove("is-active");
  els.destinationButton.classList.remove("is-active");
  els.selectionHint.textContent =
    state.config.mode === "fixture"
      ? "Synthetic demo endpoints are ready."
      : "Origin and destination selected.";
  updateEndpointLabels();
  renderEndpointMarkers();
  resetPlanForChangedInputs();
  validateForm();
}

function resetPlanForChangedInputs() {
  state.inputRevision += 1;
  if (state.planController) {
    state.planController.abort();
    state.planController = null;
  }
  if (!state.result && els.resultLoading.hidden) return;
  state.result = null;
  state.selectedOption = 0;
  clearRenderedRoute();
  showEmpty("Settings changed", "Recalculate the plan for the new vehicle or route endpoints.");
}

function showEmpty(title, message) {
  els.resultLoading.hidden = true;
  els.resultFailure.hidden = true;
  els.resultContent.hidden = true;
  els.resultEmpty.hidden = false;
  els.emptyTitle.textContent = title;
  els.emptyText.textContent = message;
  els.planButton.classList.remove("is-loading");
  els.planButtonText.textContent = "Compare routes";
  setMapStatus(state.origin && state.destination ? "Waiting for a new plan" : "Waiting for route selection");
}

function renderEndpointMarkers() {
  if (!state.map) return;
  state.endpointMarkers.forEach((marker) => marker.remove());
  state.endpointMarkers = [];
  const declared =
    state.config.mode === "fixture"
      ? state.config.endpoints
      : [state.origin, state.destination].filter(Boolean);
  declared.forEach((point) => {
    const role = point.id === "destination" ? "destination" : "origin";
    const marker = L.marker([point.latitude, point.longitude], {
      icon: endpointIcon(role),
      keyboard: true,
      title: role === "origin" ? "Origin A" : "Destination B",
    }).addTo(state.map);
    marker.bindTooltip(role === "origin" ? "Origin" : "Destination", {
      direction: "top",
      offset: [0, -21],
    });
    marker.on("click", () => {
      if (state.selectionRole === role) assignPoint(role, point);
    });
    state.endpointMarkers.push(marker);
  });
  const selected = [state.origin, state.destination].filter(Boolean);
  if (selected.length === 2 && state.routeLayers.length === 0) {
    state.map.fitBounds(
      selected.map((point) => [point.latitude, point.longitude]),
      mapPadding(),
    );
  }
}

function endpointIcon(role) {
  const label = role === "origin" ? "A" : "B";
  const css = role === "destination" ? " destination-pin" : "";
  return L.divIcon({
    className: "endpoint-marker",
    html: `<div class="marker-pin${css}"><span>${label}</span></div>`,
    iconSize: [30, 34],
    iconAnchor: [15, 31],
  });
}

function updateEndpointLabels() {
  els.originValue.textContent = pointLabel(state.origin, "Choose on map");
  els.destinationValue.textContent = pointLabel(state.destination, "Choose on map");
}

function pointLabel(point, fallback) {
  if (!point) return fallback;
  if (state.config.mode === "fixture") {
    return point.id === "origin" ? "Demo origin · A" : "Demo destination · B";
  }
  return `${point.latitude.toFixed(4)}, ${point.longitude.toFixed(4)}`;
}

function vehiclePayload() {
  return {
    usable_battery_kwh: Number(els.battery.value),
    initial_soc_pct: Number(els.initialSoc.value),
    consumption_kwh_per_100km: Number(els.consumption.value),
    max_dc_power_kw: Number(els.maxPower.value),
    reserve_soc_pct: Number(els.reserveSoc.value),
    energy_safety_factor: Number(els.safety.value),
  };
}

function validateForm() {
  if (!state.config) return false;
  const vehicle = vehiclePayload();
  let error = "";
  const mapping = {
    usable_battery_kwh: "Battery",
    initial_soc_pct: "Initial charge",
    consumption_kwh_per_100km: "Consumption",
    max_dc_power_kw: "Maximum charging power",
    reserve_soc_pct: "Arrival reserve",
    energy_safety_factor: "Safety factor",
  };
  Object.entries(mapping).some(([key, label]) => {
    const [minimum, maximum] = state.config.input_bounds[key];
    if (!Number.isFinite(vehicle[key]) || vehicle[key] < minimum || vehicle[key] > maximum) {
      error = `${label} must be between ${minimum} and ${maximum}.`;
      return true;
    }
    return false;
  });
  if (!error && vehicle.initial_soc_pct < vehicle.reserve_soc_pct) {
    error = "Initial charge cannot be lower than the arrival reserve.";
  }
  if (!error && (!state.origin || !state.destination)) {
    error = "Choose an origin and destination.";
  }
  els.formError.textContent = error;
  els.planButton.disabled = Boolean(error);
  return !error;
}

async function planRoute() {
  if (!validateForm()) return;
  const requestRevision = state.inputRevision;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 45_000);
  state.planController = controller;
  showLoading();
  try {
    const response = await fetch("/api/plan", {
      method: "POST",
      headers: {"Content-Type": "application/json", Accept: "application/json"},
      signal: controller.signal,
      body: JSON.stringify({
        origin: state.origin,
        destination: state.destination,
        vehicle: vehiclePayload(),
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(planErrorMessage(response.status, payload.error));
    if (!payload.options?.length) throw new Error("No feasible route was found for these vehicle details.");
    if (requestRevision !== state.inputRevision) return;
    state.result = payload;
    state.selectedOption = 0;
    renderResults();
  } catch (error) {
    if (requestRevision !== state.inputRevision) return;
    showFailure(
      error instanceof DOMException && error.name === "AbortError"
        ? "The route provider did not respond within 45 seconds. Try again or use local OSRM."
        : error instanceof Error
          ? error.message
          : "Could not create a route.",
    );
  } finally {
    window.clearTimeout(timeoutId);
    if (state.planController === controller) state.planController = null;
    els.planButton.classList.remove("is-loading");
    if (requestRevision === state.inputRevision) {
      validateForm();
      els.planButtonText.textContent = state.result ? "Recalculate routes" : "Try again";
    }
  }
}

function planErrorMessage(status, providerMessage) {
  if (status === 502) {
    return state.config.integration.coordinates_leave_device
      ? "The remote OSRM provider could not be reached. Try again later or use local OSRM."
      : "Local OSRM is not running. Start the road server on 127.0.0.1:5000 first.";
  }
  if (status === 422) {
    return "No energy-feasible route was found for these vehicle details and the selected endpoints.";
  }
  return providerMessage || `Could not calculate a route (${status})`;
}

function showLoading() {
  els.resultEmpty.hidden = true;
  els.resultFailure.hidden = true;
  els.resultContent.hidden = true;
  els.resultLoading.hidden = false;
  els.planButton.disabled = true;
  els.planButton.classList.add("is-loading");
  els.planButtonText.textContent = "Calculating routes…";
  setMapStatus("Calculating energy and charging plan");
}

function showFailure(message) {
  els.resultEmpty.hidden = true;
  els.resultLoading.hidden = true;
  els.resultContent.hidden = true;
  els.resultFailure.hidden = false;
  els.failureText.textContent = message;
  setMapStatus("More route information is needed");
}

function renderResults() {
  els.resultLoading.hidden = true;
  els.resultFailure.hidden = true;
  els.resultEmpty.hidden = true;
  els.resultContent.hidden = false;
  els.optionTabs.replaceChildren();
  const tabEntries = strategyTabEntries();
  state.selectedOption = Math.min(state.selectedOption, tabEntries.length - 1);
  tabEntries.forEach((entry, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.role = "tab";
    button.className = "option-tab";
    button.id = `option-tab-${index}`;
    button.setAttribute("aria-controls", "routeSummary");
    button.classList.toggle("is-active", index === state.selectedOption);
    button.textContent = strategyNames[entry.strategy] || entry.strategy;
    button.setAttribute("aria-selected", index === state.selectedOption ? "true" : "false");
    button.tabIndex = index === state.selectedOption ? 0 : -1;
    button.addEventListener("click", () => {
      state.selectedOption = index;
      renderResults();
    });
    button.addEventListener("keydown", (event) => selectOptionFromKeyboard(event, index, tabEntries.length));
    els.optionTabs.appendChild(button);
  });
  const selectedEntry = tabEntries[state.selectedOption];
  const option = state.result.options[selectedEntry.optionIndex];
  els.strategyLabel.textContent = strategyNames[selectedEntry.strategy] || selectedEntry.strategy;
  els.routeTitle.textContent = option.nodes.map(prettyNode).join(" → ");
  els.totalTime.textContent = formatMinutes(option.total_minutes);
  els.distance.textContent = `${option.total_distance_km.toFixed(0)} km`;
  els.arrivalSoc.textContent = `${option.arrival_soc_pct.toFixed(0)}%`;
  els.driveTime.textContent = formatMinutes(option.driving_minutes);
  els.chargeTime.textContent = formatMinutes(option.charging_minutes);
  els.timelineReserve.textContent = `Minimum ${Number(els.reserveSoc.value).toFixed(0)}%`;
  renderTimeline(option.soc_timeline);
  renderCharging(option.charging_stops);
  const unavailable = state.result.unavailable_strategies || [];
  els.unavailableBox.hidden = unavailable.length === 0;
  els.unavailableText.textContent = unavailable
    .map((name) => strategyNames[name] || name)
    .join(", ");
  const modelNotes = [
    "Results use static station records and an estimated consumption model. They do not include live availability, pricing, or reservation information.",
  ];
  const resolution = state.result.model_resolution || {};
  if (resolution.refined_after_coarse_infeasible) {
    modelNotes.push(
      "The initial 5% SOC grid found no feasible route, so this result was recalculated at 2% SOC resolution.",
    );
  }
  const selection = state.result.candidate_selection || {};
  if (selection.expanded_after_infeasible) {
    modelNotes.push(
      `The local candidate search widened from ${selection.initial_cap} to ${selection.selected_count} stations after the initial capped search was infeasible.`,
    );
  } else if (selection.selected_count && selection.selected_count < selection.eligible_count) {
    modelNotes.push(
      `The route uses ${selection.selected_count} of ${selection.eligible_count} eligible corridor stations; the candidate cap is a planning boundary.`,
    );
  }
  els.modelBoundary.textContent = modelNotes.join(" ");
  renderRoute(option);
  setMapStatus(
    `${tabEntries.length} route strategies ready`,
  );
}

function strategyTabEntries() {
  return strategyOrder.flatMap((strategy) => {
    const optionIndex = state.result.options.findIndex((option) => option.strategies.includes(strategy));
    return optionIndex === -1 ? [] : [{strategy, optionIndex}];
  });
}

function selectOptionFromKeyboard(event, currentIndex, tabCount) {
  const lastIndex = tabCount - 1;
  const targets = {
    ArrowLeft: currentIndex === 0 ? lastIndex : currentIndex - 1,
    ArrowRight: currentIndex === lastIndex ? 0 : currentIndex + 1,
    Home: 0,
    End: lastIndex,
  };
  if (!(event.key in targets)) return;
  event.preventDefault();
  state.selectedOption = targets[event.key];
  renderResults();
  document.getElementById(`option-tab-${state.selectedOption}`).focus();
}

function renderTimeline(entries) {
  els.socTimeline.replaceChildren();
  entries.forEach((entry) => {
    const node = document.createElement("div");
    node.className = `soc-stop${entry.kind === "charge" ? " is-charge" : ""}`;
    const dot = document.createElement("span");
    dot.className = "soc-dot";
    const copy = document.createElement("span");
    copy.className = "soc-copy";
    const label = document.createElement("strong");
    label.textContent = translateTimelineLabel(entry.label);
    const detail = document.createElement("small");
    detail.textContent = entry.kind === "charge" ? "After charging" : "Estimated battery";
    copy.append(label, detail);
    const value = document.createElement("span");
    value.className = "soc-value";
    value.textContent = `${Number(entry.soc_pct).toFixed(0)}%`;
    node.append(dot, copy, value);
    els.socTimeline.appendChild(node);
  });
}

function renderCharging(stops) {
  els.chargingPlan.replaceChildren();
  els.stopCount.textContent = stops.length ? `${stops.length} ${stops.length === 1 ? "stop" : "stops"}` : "No stops";
  if (!stops.length) {
    const card = document.createElement("div");
    card.className = "no-charge-stop";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = "No charging stop needed";
    const detail = document.createElement("small");
    detail.textContent = "The model completes the selected route with the available energy.";
    copy.append(title, detail);
    card.appendChild(copy);
    els.chargingPlan.appendChild(card);
    return;
  }
  stops.forEach((stop) => {
    const card = document.createElement("div");
    card.className = "charge-stop";
    const copy = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = stop.name;
    const detail = document.createElement("small");
    detail.textContent = `${stop.arrival_soc_pct.toFixed(0)}% → ${stop.departure_soc_pct.toFixed(0)}% · ${stop.energy_added_kwh.toFixed(1)} kWh estimated`;
    copy.append(name, detail);
    const duration = document.createElement("span");
    duration.className = "charge-time";
    duration.textContent = formatMinutes(stop.charging_minutes);
    card.append(copy, duration);
    els.chargingPlan.appendChild(card);
  });
}

function renderRoute(option) {
  if (!state.map) return;
  clearRenderedRoute();
  const halo = L.geoJSON(option.geometry, {
    style: {
      color: "#ffffff",
      weight: 12,
      opacity: 0.82,
      lineCap: "round",
      lineJoin: "round",
    },
  }).addTo(state.map);
  const route = L.geoJSON(option.geometry, {
    style: {
      color: "#0b765a",
      weight: 6,
      opacity: 0.96,
      lineCap: "round",
      lineJoin: "round",
    },
  }).addTo(state.map);
  halo.bringToBack();
  route.bringToFront();
  state.routeLayers.push(halo, route);
  option.charging_stops.forEach((stop, index) => {
    const marker = L.marker([stop.latitude, stop.longitude], {
      icon: L.divIcon({
        className: "endpoint-marker",
        html: `<div class="marker-pin stop-pin"><span>${index + 1}</span></div>`,
        iconSize: [28, 32],
        iconAnchor: [14, 29],
      }),
      title: stop.name,
    })
      .addTo(state.map)
      .bindPopup(
        `<strong>${escapeHtml(stop.name)}</strong><br>${stop.arrival_soc_pct.toFixed(0)}% → ${stop.departure_soc_pct.toFixed(0)}% estimated`,
      );
    state.stopMarkers.push(marker);
  });
  fitVisibleRoute();
}

function clearRenderedRoute() {
  [...state.routeLayers, ...state.stopMarkers].forEach((layer) => layer.remove());
  state.routeLayers = [];
  state.stopMarkers = [];
}

function fitVisibleRoute() {
  if (!state.map) return;
  if (state.routeLayers.length) {
    state.map.fitBounds(state.routeLayers[0].getBounds(), mapPadding());
    return;
  }
  const points = [state.origin, state.destination].filter(Boolean);
  if (points.length) {
    state.map.fitBounds(
      points.map((point) => [point.latitude, point.longitude]),
      mapPadding(),
    );
  }
}

function mapPadding() {
  if (window.innerWidth > 1120) {
    return {paddingTopLeft: [55, 60], paddingBottomRight: [435, 60]};
  }
  if (window.innerWidth > 820) {
    return {paddingTopLeft: [45, 45], paddingBottomRight: [45, 300]};
  }
  return {padding: [35, 55]};
}

function setMapStatus(message) {
  els.mapStatus.replaceChildren();
  const dot = document.createElement("span");
  dot.className = "status-pulse";
  dot.setAttribute("aria-hidden", "true");
  els.mapStatus.append(dot, document.createTextNode(message));
}

function translateTimelineLabel(value) {
  const normalized = String(value).toLowerCase();
  if (normalized === "start" || normalized === "origin") return "Origin";
  if (normalized === "arrival" || normalized === "destination") return "Destination";
  return value.replace(/^epdk:/, "").replaceAll("_", " ");
}

function prettyNode(value) {
  if (value === "origin") return "Origin";
  if (value === "destination") return "Destination";
  return value
    .replace(/^epdk:/, "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatMinutes(value) {
  const minutes = Math.round(value);
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (!hours) return `${remainder} min`;
  return remainder ? `${hours} hr ${remainder} min` : `${hours} hr`;
}

function roundCoordinate(value) {
  return Math.round(value * 1e6) / 1e6;
}

function escapeHtml(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span.innerHTML;
}
