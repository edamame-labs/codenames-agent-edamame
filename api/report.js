import { issueSignedToken, presignUrl, put } from "@vercel/blob";
import { Resend } from "resend";
import crypto from "node:crypto";


const MAX_BODY_BYTES = 64 * 1024;
const MAX_MESSAGE = 2000;
const MAX_HISTORY = 80;
const LINK_LIFETIME_MS = 7 * 24 * 60 * 60 * 1000;
const PRODUCTION_ORIGIN = "https://codenames-agent-cluecast.vercel.app";


function text(value, limit) {
  return String(value ?? "").trim().slice(0, limit);
}


function redact(value) {
  return text(value, MAX_MESSAGE)
    .replace(/\b(?:sk|re)_[A-Za-z0-9_-]{16,}\b/g, "[REDACTED]")
    .replace(/\bBearer\s+[A-Za-z0-9._~-]{16,}\b/gi, "Bearer [REDACTED]");
}


function cleanScalar(value, limit = 120) {
  if (typeof value === "boolean" || typeof value === "number") return value;
  return text(value, limit);
}


function cleanObject(value, allowed, limit = 120) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return Object.fromEntries(
    allowed
      .filter((key) => value[key] !== undefined && value[key] !== null)
      .map((key) => [key, cleanScalar(value[key], limit)]),
  );
}


function cleanHistory(history) {
  if (!Array.isArray(history)) return [];
  return history.slice(-MAX_HISTORY).map((row) => (
    Array.isArray(row) ? row.slice(0, 5).map((item) => cleanScalar(item)) : []
  ));
}


function cleanCells(cells) {
  if (!Array.isArray(cells)) return [];
  return cells.slice(0, 25).map((cell, index) => {
    const revealed = Boolean(cell && cell.revealed);
    return {
      index,
      word: text(cell && cell.word, 64),
      revealed,
      // Never persist an unrevealed key, even from a spymaster report.
      label: revealed ? text(cell && cell.label, 16) || null : null,
    };
  });
}


export function sanitizeReport(input, requestContext = {}) {
  const source = input && typeof input === "object" ? input : {};
  const game = source.game && typeof source.game === "object" ? source.game : {};
  const contact = text(source.contact, 254);
  return {
    schema: "cluecast-problem-report-v1",
    received_at: new Date().toISOString(),
    message: redact(source.message),
    contact: /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact) ? contact : null,
    page: {
      url: text(source.page && source.page.url, 500),
      viewport: cleanObject(source.page && source.page.viewport, ["width", "height"]),
      user_agent: text(source.page && source.page.user_agent, 500),
    },
    game: {
      active: Boolean(game.active),
      mode: game.single_team ? "single_team" : "two_team",
      status: text(game.status, 40),
      turn: text(game.turn, 16),
      phase: text(game.phase, 16),
      acting_seat: text(game.acting_seat, 32) || null,
      winner: text(game.winner, 8) || null,
      end: cleanObject(game.end, ["reason", "word", "by"]),
      seats: cleanObject(game.seats, ["red_cm", "red_g", "blue_cm", "blue_g"], 16),
      counts: cleanObject(game.counts, ["red_left", "blue_left"]),
      pending: cleanObject(game.pending, ["clue", "number", "made"]),
      red_clues: Number.isFinite(Number(game.red_clues)) ? Number(game.red_clues) : null,
      hints_remaining: Number.isFinite(Number(game.hints_remaining))
        ? Number(game.hints_remaining)
        : null,
      history: cleanHistory(game.history),
      cells: cleanCells(game.cells),
    },
    ui: {
      pending_ack: cleanObject(source.ui && source.ui.pending_ack, ["label", "team", "clue", "number"]),
      retry_needed: Boolean(source.ui && source.ui.retry_needed),
      suggested_words: Array.isArray(source.ui && source.ui.suggested_words)
        ? source.ui.suggested_words.slice(0, 5).map((word) => text(word, 64))
        : [],
    },
    request: {
      origin: text(requestContext.origin, 300),
      country: text(requestContext.country, 8) || null,
    },
  };
}


function parseBody(request) {
  const contentLength = Number(request.headers["content-length"] || 0);
  if (contentLength > MAX_BODY_BYTES) throw new Error("report_too_large");
  if (request.body && typeof request.body === "object" && !Buffer.isBuffer(request.body)) {
    return request.body;
  }
  const raw = Buffer.isBuffer(request.body)
    ? request.body.toString("utf8")
    : String(request.body || "{}");
  if (Buffer.byteLength(raw) > MAX_BODY_BYTES) throw new Error("report_too_large");
  return JSON.parse(raw || "{}");
}


function allowedOrigin(origin) {
  if (!origin) return true;
  const allowed = new Set([PRODUCTION_ORIGIN]);
  if (process.env.VERCEL_URL) allowed.add(`https://${process.env.VERCEL_URL}`);
  if (process.env.VERCEL_PROJECT_PRODUCTION_URL) {
    allowed.add(`https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`);
  }
  return allowed.has(origin) || /^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/.test(origin);
}


function htmlEscape(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}


function respond(response, status, body) {
  response.setHeader("Cache-Control", "no-store");
  return response.status(status).json(body);
}


export default async function handler(request, response) {
  if (request.method !== "POST") {
    response.setHeader("Allow", "POST");
    return respond(response, 405, { error: "method not allowed" });
  }
  if (!allowedOrigin(request.headers.origin)) {
    return respond(response, 403, { error: "origin not allowed" });
  }

  let input;
  try {
    input = parseBody(request);
  } catch (error) {
    return respond(
      response,
      error.message === "report_too_large" ? 413 : 400,
      { error: error.message === "report_too_large" ? "report is too large" : "invalid report" },
    );
  }
  // Honeypot: appear successful without storing or emailing bot submissions.
  if (text(input.website, 200)) {
    return respond(response, 200, { ok: true, report_id: "received" });
  }
  if (redact(input.message).length < 3) {
    return respond(response, 400, { error: "please describe the problem" });
  }

  const to = text(process.env.CLUECAST_REPORT_TO, 254);
  const from = text(
    process.env.CLUECAST_REPORT_FROM || "ClueCast Reports <reports@edamamelabs.com>",
    254,
  );
  if (!process.env.BLOB_READ_WRITE_TOKEN || !process.env.RESEND_API_KEY || !to) {
    return respond(response, 503, { error: "problem reporting is not configured" });
  }

  const reportId = crypto.randomUUID();
  const report = {
    id: reportId,
    ...sanitizeReport(input, {
      origin: request.headers.origin,
      country: request.headers["x-vercel-ip-country"],
    }),
  };
  const day = report.received_at.slice(0, 10);
  const pathname = `reports/${day}/${report.received_at.replace(/[:.]/g, "-")}-${reportId}.json`;

  let blob;
  try {
    blob = await put(pathname, JSON.stringify(report, null, 2), {
      access: "private",
      addRandomSuffix: false,
      contentType: "application/json",
      cacheControlMaxAge: 60,
    });
  } catch (error) {
    console.error("cluecast_report storage_failed", reportId, error && error.name);
    return respond(response, 502, { error: "could not save the report" });
  }

  const validUntil = Date.now() + LINK_LIFETIME_MS;
  let privateUrl;
  try {
    const signedToken = await issueSignedToken({
      pathname: blob.pathname,
      operations: ["get"],
      validUntil,
    });
    const signed = await presignUrl(signedToken, {
      operation: "get",
      pathname: blob.pathname,
      access: "private",
      validUntil,
      useCache: false,
    });
    privateUrl = signed.presignedUrl;
  } catch (error) {
    console.error("cluecast_report link_failed", reportId, error && error.name);
    return respond(response, 502, {
      error: "report saved, but its private link could not be created",
      report_id: reportId,
      saved: true,
    });
  }

  const mode = report.game.active
    ? `${report.game.mode} · ${report.game.status || "unknown state"}`
    : "no active game";
  const resend = new Resend(process.env.RESEND_API_KEY);
  try {
    const result = await resend.emails.send({
      from,
      to: [to],
      subject: `ClueCast problem report ${reportId.slice(0, 8)}`,
      text: [
        report.message,
        "",
        `Game: ${mode}`,
        `Report ID: ${reportId}`,
        `Private log (expires in 7 days): ${privateUrl}`,
        report.contact ? `Reporter contact: ${report.contact}` : "",
      ].filter(Boolean).join("\n"),
      html: `
        <h2>ClueCast problem report</h2>
        <p>${htmlEscape(report.message).replace(/\n/g, "<br>")}</p>
        <p><strong>Game:</strong> ${htmlEscape(mode)}</p>
        <p><strong>Report ID:</strong> <code>${htmlEscape(reportId)}</code></p>
        <p><a href="${htmlEscape(privateUrl)}">Open the private game log</a>
        <small>(link expires in 7 days)</small></p>
        ${report.contact ? `<p><strong>Reporter:</strong> ${htmlEscape(report.contact)}</p>` : ""}
      `,
    });
    if (result.error) throw new Error(result.error.message || "resend_error");
  } catch (error) {
    console.error("cluecast_report email_failed", reportId, error && error.name);
    return respond(response, 502, {
      error: "report saved, but email notification failed",
      report_id: reportId,
      saved: true,
    });
  }

  console.log("cluecast_report sent", reportId);
  return respond(response, 200, { ok: true, report_id: reportId, saved: true });
}
