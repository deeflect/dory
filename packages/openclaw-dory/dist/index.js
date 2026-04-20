import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { jsonResult, listMemoryCorpusSupplements, readNumberParam, readStringParam, resolveMemorySearchConfig, resolveSessionAgentId, } from "openclaw/plugin-sdk/memory-core-host-runtime-core";
const managerCache = new Map();
const PLUGIN_ID = "dory-memory";
const CONFIG_SCHEMA = {
    type: "object",
    additionalProperties: false,
    properties: {
        baseUrl: {
            type: "string",
            description: "Base URL for the Dory HTTP server.",
        },
        token: {
            type: "string",
            description: "Optional bearer token for Dory HTTP.",
        },
    },
    required: ["baseUrl"],
};
const MemorySearchSchema = {
    type: "object",
    additionalProperties: false,
    properties: {
        query: { type: "string" },
        maxResults: { type: "number" },
        minScore: { type: "number" },
        corpus: { enum: ["memory", "wiki", "all"] },
    },
    required: ["query"],
};
const MemoryGetSchema = {
    type: "object",
    additionalProperties: false,
    properties: {
        path: { type: "string" },
        from: { type: "number" },
        lines: { type: "number" },
        corpus: { enum: ["memory", "wiki", "all"] },
    },
    required: ["path"],
};
const MemoryWriteSchema = {
    type: "object",
    additionalProperties: false,
    properties: {
        action: { enum: ["write", "replace", "forget"] },
        kind: { enum: ["fact", "preference", "state", "decision", "note"] },
        subject: { type: "string" },
        content: { type: "string" },
        scope: { enum: ["core", "person", "project", "concept", "decision"] },
        confidence: { type: "string" },
        source: { type: "string" },
        soft: { type: "boolean" },
        dry_run: { type: "boolean" },
        force_inbox: { type: "boolean" },
        allow_canonical: { type: "boolean" },
        reason: { type: "string" },
    },
    required: ["action", "kind", "subject", "content"],
};
async function request(options, path, init) {
    const headers = {
        "Content-Type": "application/json",
    };
    if (options.token) {
        headers.Authorization = `Bearer ${options.token}`;
    }
    const response = await fetch(new URL(path, options.baseUrl), {
        method: init.method,
        headers,
        body: init.body ? JSON.stringify(init.body) : undefined,
    });
    if (!response.ok) {
        const detail = (await response.text()).trim();
        throw new Error(`dory request failed: ${response.status} ${response.statusText}${detail ? ` - ${detail}` : ""}`);
    }
    return (await response.json());
}
export function wake(options, body) {
    return request(options, "/v1/wake", { method: "POST", body });
}
export function search(options, body) {
    return request(options, "/v1/search", { method: "POST", body });
}
export function activeMemory(options, body) {
    return request(options, "/v1/active-memory", { method: "POST", body });
}
export function get(options, path, fromLine = 1, lines) {
    const params = new URLSearchParams({
        path,
        from: String(fromLine),
    });
    if (lines !== undefined) {
        params.set("lines", String(lines));
    }
    return request(options, `/v1/get?${params.toString()}`, { method: "GET" });
}
export function write(options, body) {
    return request(options, "/v1/write", { method: "POST", body });
}
export function memoryWrite(options, body) {
    return request(options, "/v1/memory-write", { method: "POST", body });
}
export function status(options) {
    return request(options, "/v1/status", { method: "GET" });
}
export function recordRecallEvent(options, body) {
    return request(options, "/v1/recall-event", { method: "POST", body });
}
export function getPublicArtifacts(options) {
    return request(options, "/v1/public-artifacts", { method: "GET" });
}
function resolveAgentId(ctx) {
    const explicit = typeof ctx.agentId === "string" ? ctx.agentId.trim() : "";
    if (explicit) {
        return explicit;
    }
    return ctx.config ? resolveSessionAgentId({ sessionKey: ctx.sessionKey, config: ctx.config }) : "";
}
function resolveMemoryToolContext(ctx) {
    if (!ctx.config) {
        return null;
    }
    const agentId = resolveAgentId(ctx);
    if (!agentId || !resolveMemorySearchConfig(ctx.config, agentId)) {
        return null;
    }
    return {
        agentId,
        agentSessionKey: ctx.sessionKey,
    };
}
function buildMemorySearchUnavailableResult(error) {
    const reason = (error ?? "memory search unavailable").trim() || "memory search unavailable";
    return {
        results: [],
        disabled: true,
        unavailable: true,
        error: reason,
        warning: "Memory search is unavailable due to a Dory backend error.",
        action: "Check the Dory service and retry memory_search.",
        debug: {
            warning: "Memory search is unavailable due to a Dory backend error.",
            action: "Check the Dory service and retry memory_search.",
            error: reason,
        },
    };
}
function hasOwnRecordKey(record, key) {
    return Object.prototype.hasOwnProperty.call(record, key);
}
function readBooleanParam(params, key) {
    const value = params[key];
    return typeof value === "boolean" ? value : undefined;
}
function readRecordParam(params, key) {
    const value = params[key];
    return value && typeof value === "object" && !Array.isArray(value)
        ? value
        : undefined;
}
function slugifyPathSegment(value) {
    return value
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 80);
}
function titleFromTarget(target) {
    const fileName = target.split("/").at(-1) ?? "memory-note.md";
    const stem = fileName.replace(/\.md$/i, "");
    const words = stem
        .split(/[-_]+/g)
        .filter(Boolean)
        .map((part) => part.replace(/^\d{4}$/, "").replace(/^\d{2}$/, ""))
        .filter(Boolean);
    if (words.length === 0) {
        return "Memory Note";
    }
    return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}
function titleFromContent(content) {
    const firstLine = content
        .split("\n")
        .map((line) => line.trim())
        .find(Boolean);
    if (!firstLine) {
        return "Memory Note";
    }
    return firstLine.replace(/^[-*]\s*/, "").slice(0, 80).trim() || "Memory Note";
}
function todayIsoDate() {
    return new Date().toISOString().slice(0, 10);
}
function inferMemoryWriteTarget(params) {
    const explicitTarget = params.target?.trim();
    if (explicitTarget) {
        return explicitTarget;
    }
    const title = params.title?.trim() || titleFromContent(params.content);
    const slug = slugifyPathSegment(title) || "memory-note";
    const today = todayIsoDate();
    if (params.type === "person") {
        return `people/${slug}.md`;
    }
    if (params.type === "project") {
        return `projects/${slug}.md`;
    }
    if (params.type === "knowledge") {
        return `knowledge/${slug}.md`;
    }
    if (params.type === "reference") {
        return `references/${slug}.md`;
    }
    if (params.type === "decision") {
        return `decisions/${today}-${slug}.md`;
    }
    if (params.type === "weekly") {
        return `logs/weekly/${today}-${slug}.md`;
    }
    if (params.type === "session") {
        return `logs/sessions/${today}-${slug}.md`;
    }
    if (params.type === "core") {
        return `core/${slug}.md`;
    }
    return `logs/daily/${today}-${slug}.md`;
}
function buildDefaultWriteFrontmatter(params) {
    const resolvedTitle = params.title?.trim() || titleFromTarget(params.target) || titleFromContent(params.content);
    const defaultType = params.type?.trim()
        || (params.target.startsWith("people/") ? "person"
            : params.target.startsWith("projects/") ? "project"
                : params.target.startsWith("knowledge/") ? "knowledge"
                    : params.target.startsWith("references/") ? "reference"
                        : params.target.startsWith("decisions/") ? "decision"
                            : params.target.startsWith("logs/weekly/") ? "weekly"
                                : params.target.startsWith("logs/sessions/") ? "session"
                                    : params.target.startsWith("core/") ? "core"
                                        : "daily");
    const frontmatter = {
        ...(params.frontmatter ?? {}),
        title: resolvedTitle,
        type: defaultType,
    };
    if (params.status?.trim()) {
        frontmatter.status = params.status.trim();
    }
    else if (defaultType === "daily" && !hasOwnRecordKey(frontmatter, "date")) {
        frontmatter.date = todayIsoDate();
    }
    return frontmatter;
}
async function searchMemoryCorpusSupplements(params) {
    if (params.corpus === "memory") {
        return [];
    }
    const supplements = listMemoryCorpusSupplements();
    if (supplements.length === 0) {
        return [];
    }
    const results = (await Promise.all(supplements.map(async (registration) => await registration.supplement.search({
        query: params.query,
        maxResults: params.maxResults,
        agentSessionKey: params.agentSessionKey,
    })))).flat();
    return [...results]
        .sort((left, right) => {
        const leftScore = Number(left.score ?? 0);
        const rightScore = Number(right.score ?? 0);
        if (leftScore !== rightScore) {
            return rightScore - leftScore;
        }
        return String(left.path ?? "").localeCompare(String(right.path ?? ""));
    })
        .slice(0, Math.max(1, params.maxResults ?? 10));
}
async function getMemoryCorpusSupplementResult(params) {
    if (params.corpus === "memory") {
        return null;
    }
    for (const registration of listMemoryCorpusSupplements()) {
        const result = await registration.supplement.get({
            lookup: params.lookup,
            fromLine: params.fromLine,
            lineCount: params.lineCount,
            agentSessionKey: params.agentSessionKey,
        });
        if (result) {
            return result;
        }
    }
    return null;
}
function createMemorySearchTool(options, ctx) {
    const toolCtx = resolveMemoryToolContext(ctx);
    if (!toolCtx) {
        return null;
    }
    return {
        label: "Memory Search",
        name: "memory_search",
        description: "Mandatory recall step: semantically search durable memory before answering questions about prior work, decisions, dates, people, preferences, or todos.",
        parameters: MemorySearchSchema,
        execute: async (_toolCallId, params) => {
            const query = readStringParam(params, "query", { required: true });
            const maxResults = readNumberParam(params, "maxResults");
            const minScore = readNumberParam(params, "minScore");
            const requestedCorpus = readStringParam(params, "corpus");
            const shouldQueryMemory = requestedCorpus !== "wiki";
            const shouldQuerySupplements = requestedCorpus === "wiki" || requestedCorpus === "all";
            let memoryResults = [];
            if (shouldQueryMemory) {
                try {
                    const manager = getDoryManager(options, toolCtx.agentId);
                    memoryResults = (await manager.search(query, {
                        maxResults,
                        minScore,
                        sessionKey: toolCtx.agentSessionKey,
                    })).map((result) => ({
                        ...result,
                        corpus: "memory",
                    }));
                }
                catch (error) {
                    if (!shouldQuerySupplements) {
                        const message = error instanceof Error ? error.message : String(error);
                        return jsonResult(buildMemorySearchUnavailableResult(message));
                    }
                }
            }
            const supplementResults = shouldQuerySupplements
                ? await searchMemoryCorpusSupplements({
                    query,
                    maxResults,
                    agentSessionKey: toolCtx.agentSessionKey,
                    corpus: requestedCorpus,
                })
                : [];
            const results = [...memoryResults, ...supplementResults]
                .sort((left, right) => {
                const leftScore = Number(left.score ?? 0);
                const rightScore = Number(right.score ?? 0);
                if (leftScore !== rightScore) {
                    return rightScore - leftScore;
                }
                return String(left.path ?? "").localeCompare(String(right.path ?? ""));
            })
                .slice(0, Math.max(1, maxResults ?? 10));
            void emitRecallEvent(options, {
                agent: toolCtx.agentId,
                session_key: toolCtx.agentSessionKey,
                query,
                result_paths: results
                    .map((item) => String(item.path ?? "").trim())
                    .filter((item) => item.length > 0),
                selected_path: results.length === 1 ? String(results[0]?.path ?? "") : undefined,
                corpus: requestedCorpus ?? "memory",
                source: "openclaw-recall",
            });
            return jsonResult({
                results,
                provider: "dory-http",
                mode: "hybrid",
            });
        },
    };
}
function createMemoryGetTool(options, ctx) {
    const toolCtx = resolveMemoryToolContext(ctx);
    if (!toolCtx) {
        return null;
    }
    return {
        label: "Memory Get",
        name: "memory_get",
        description: "Read a small excerpt from durable memory after search, keeping context tight and cited.",
        parameters: MemoryGetSchema,
        execute: async (_toolCallId, params) => {
            const relPath = readStringParam(params, "path", { required: true });
            const from = readNumberParam(params, "from", { integer: true });
            const lines = readNumberParam(params, "lines", { integer: true });
            const requestedCorpus = readStringParam(params, "corpus");
            if (requestedCorpus === "wiki" || requestedCorpus === "all") {
                const supplement = await getMemoryCorpusSupplementResult({
                    lookup: relPath,
                    fromLine: from ?? undefined,
                    lineCount: lines ?? undefined,
                    agentSessionKey: toolCtx.agentSessionKey,
                    corpus: requestedCorpus,
                });
                if (supplement && requestedCorpus === "wiki") {
                    const { content, ...rest } = supplement;
                    return jsonResult({
                        ...rest,
                        text: typeof content === "string" ? content : "",
                    });
                }
            }
            try {
                const manager = getDoryManager(options, toolCtx.agentId);
                const payload = await manager.readFile({
                    relPath,
                    from: from ?? undefined,
                    lines: lines ?? undefined,
                });
                void emitRecallEvent(options, {
                    agent: toolCtx.agentId,
                    session_key: toolCtx.agentSessionKey,
                    query: relPath,
                    result_paths: [payload.path],
                    selected_path: payload.path,
                    corpus: requestedCorpus ?? "memory",
                    source: "openclaw-recall",
                });
                return jsonResult(payload);
            }
            catch (error) {
                if (requestedCorpus === "all") {
                    const supplement = await getMemoryCorpusSupplementResult({
                        lookup: relPath,
                        fromLine: from ?? undefined,
                        lineCount: lines ?? undefined,
                        agentSessionKey: toolCtx.agentSessionKey,
                        corpus: requestedCorpus,
                    });
                    if (supplement) {
                        const { content, ...rest } = supplement;
                        return jsonResult({
                            ...rest,
                            text: typeof content === "string" ? content : "",
                        });
                    }
                }
                const message = error instanceof Error ? error.message : String(error);
                return jsonResult({
                    path: relPath,
                    text: "",
                    disabled: true,
                    error: message,
                });
            }
        },
    };
}
function createMemoryWriteTool(options, ctx) {
    const toolCtx = resolveMemoryToolContext(ctx);
    if (!toolCtx) {
        return null;
    }
    return {
        label: "Memory Write",
        name: "memory_write",
        description: "Persist semantic durable memory to Dory when the user explicitly asks you to remember, save, update, or forget something for later recall.",
        parameters: MemoryWriteSchema,
        execute: async (_toolCallId, params) => {
            const action = readStringParam(params, "action", { required: true });
            const kind = readStringParam(params, "kind", { required: true });
            const subject = readStringParam(params, "subject", { required: true });
            const content = readStringParam(params, "content", { required: true });
            const scope = readStringParam(params, "scope");
            const confidence = readStringParam(params, "confidence");
            const source = readStringParam(params, "source");
            const reason = readStringParam(params, "reason");
            const soft = readBooleanParam(params, "soft") ?? false;
            const dryRun = readBooleanParam(params, "dry_run");
            const forceInbox = readBooleanParam(params, "force_inbox");
            const allowCanonical = readBooleanParam(params, "allow_canonical");
            try {
                const result = await memoryWrite(options, {
                    action,
                    kind,
                    subject,
                    content,
                    scope,
                    confidence,
                    source,
                    soft,
                    dry_run: dryRun,
                    force_inbox: forceInbox,
                    allow_canonical: allowCanonical,
                    agent: toolCtx.agentId,
                    session_id: toolCtx.agentSessionKey,
                    reason,
                });
                return jsonResult({
                    ok: true,
                    provider: "dory-http",
                    subject,
                    ...result,
                });
            }
            catch (error) {
                const message = error instanceof Error ? error.message : String(error);
                return jsonResult({
                    ok: false,
                    provider: "dory-http",
                    subject,
                    error: message,
                });
            }
        },
    };
}
export class DoryMemorySearchManager {
    options;
    agentId;
    statusSnapshot;
    statusRefreshedAtMs;
    constructor(options, agentId) {
        this.options = options;
        this.agentId = agentId;
        this.statusRefreshedAtMs = null;
        this.statusSnapshot = {
            backend: "qmd",
            provider: "dory-http",
            vector: {
                enabled: true,
                available: undefined,
            },
            custom: {
                baseUrl: options.baseUrl,
                statusSource: "cold",
                statusAgeMs: null,
                statusStale: true,
            },
        };
    }
    async search(query, opts) {
        const mode = mapSearchMode(opts?.qmdSearchModeOverride);
        opts?.onDebug?.({
            provider: "dory-http",
            mode,
            sessionKeyApplied: false,
            sessionKeySupported: false,
            warning: opts?.sessionKey ? "sessionKey is not yet supported by Dory HTTP search" : undefined,
        });
        const payload = await search(this.options, {
            query,
            k: opts?.maxResults ?? 10,
            mode,
            min_score: opts?.minScore,
        });
        const warnings = Array.isArray(payload.warnings)
            ? payload.warnings.filter((value) => typeof value === "string" && value.trim().length > 0)
            : [];
        if (warnings.length > 0) {
            opts?.onDebug?.({
                provider: "dory-http",
                warnings,
            });
        }
        const results = Array.isArray(payload.results) ? payload.results : [];
        const minScore = opts?.minScore ?? Number.NEGATIVE_INFINITY;
        return results
            .map((item) => mapSearchResult(item))
            .filter((item) => item.score >= minScore);
    }
    async activeMemory(prompt, opts) {
        return activeMemory(this.options, {
            prompt,
            agent: opts?.agent ?? this.agentId,
            budget_tokens: opts?.budgetTokens,
            cwd: opts?.cwd,
            timeout_ms: opts?.timeoutMs,
        });
    }
    async readFile(params) {
        const payload = await get(this.options, params.relPath, params.from ?? 1, params.lines);
        return {
            text: String(payload.content ?? ""),
            path: String(payload.path ?? params.relPath),
        };
    }
    status() {
        const ageMs = this.statusRefreshedAtMs === null ? null : Math.max(0, Date.now() - this.statusRefreshedAtMs);
        const stale = ageMs === null || ageMs > 30_000;
        this.statusSnapshot = {
            ...this.statusSnapshot,
            custom: {
                ...(this.statusSnapshot.custom ?? {}),
                statusAgeMs: ageMs,
                statusStale: stale,
            },
        };
        return this.statusSnapshot;
    }
    async refreshStatus() {
        const payload = await status(this.options);
        const openclaw = payload.openclaw ?? {};
        const refreshedAtMs = Date.now();
        this.statusRefreshedAtMs = refreshedAtMs;
        this.statusSnapshot = {
            ...this.statusSnapshot,
            files: Number(payload.files_indexed ?? 0),
            chunks: Number(payload.chunks_indexed ?? 0),
            vector: {
                enabled: true,
                available: Number(payload.vectors_indexed ?? 0) > 0,
            },
            custom: {
                ...(this.statusSnapshot.custom ?? {}),
                statusSource: "refreshed",
                statusCheckedAt: new Date(refreshedAtMs).toISOString(),
                statusAgeMs: 0,
                statusStale: false,
                vectorsIndexed: Number(payload.vectors_indexed ?? 0),
                flushEnabled: Boolean(openclaw.flush_enabled),
                recallTrackingEnabled: Boolean(openclaw.recall_tracking_enabled),
                artifactListingEnabled: Boolean(openclaw.artifact_listing_enabled),
                recentRecallCount: Number(openclaw.recent_recall_count ?? 0),
                lastRecallEventAt: typeof openclaw.last_recall_event_at === "string"
                    ? openclaw.last_recall_event_at
                    : undefined,
                lastRecallSelectedPath: typeof openclaw.last_recall_selected_path === "string"
                    ? openclaw.last_recall_selected_path
                    : undefined,
                lastFlushStatus: typeof openclaw.last_flush_status === "string"
                    ? openclaw.last_flush_status
                    : undefined,
                recentBackendError: typeof openclaw.recent_backend_error === "string"
                    ? openclaw.recent_backend_error
                    : undefined,
            },
        };
        return this.statusSnapshot;
    }
    async sync() {
        await this.refreshStatus().catch(() => undefined);
    }
    async probeEmbeddingAvailability() {
        const next = await this.refreshStatus().catch(() => null);
        if (!next) {
            return {
                ok: false,
                error: "unable to refresh Dory status before probing embeddings",
            };
        }
        if (next.vector?.available === true) {
            return { ok: true };
        }
        return {
            ok: false,
            error: "Dory has no indexed vectors available for embedding-backed search yet",
        };
    }
    async probeVectorAvailability() {
        const next = await this.refreshStatus().catch(() => null);
        if (!next) {
            return false;
        }
        return next.vector?.available === true;
    }
    async close() {
        this.statusSnapshot = {
            ...this.statusSnapshot,
            custom: {
                ...(this.statusSnapshot.custom ?? {}),
                statusSource: "closed",
                statusStale: false,
            },
        };
        return;
    }
}
function mapSearchMode(mode) {
    if (mode === "query") {
        return "bm25";
    }
    if (mode === "vsearch") {
        return "vector";
    }
    return "hybrid";
}
function getDoryManager(options, agentId) {
    const key = `default:${agentId}`;
    let manager = managerCache.get(key);
    if (!manager) {
        manager = new DoryMemorySearchManager(options, agentId);
        managerCache.set(key, manager);
    }
    return manager;
}
export function buildDoryMemoryCapability(options) {
    return {
        promptBuilder: ({ availableTools, citationsMode }) => {
            const hasMemorySearch = availableTools.has("memory_search");
            const hasMemoryGet = availableTools.has("memory_get");
            const hasMemoryWrite = availableTools.has("memory_write");
            if (!hasMemorySearch && !hasMemoryGet && !hasMemoryWrite) {
                return [];
            }
            let toolGuidance;
            if (hasMemorySearch && hasMemoryGet) {
                toolGuidance =
                    "Before answering anything about prior work, decisions, dates, people, preferences, or todos: run memory_search against Dory-backed memory first, then use memory_get to pull only the needed lines.";
            }
            else if (hasMemorySearch) {
                toolGuidance =
                    "Before answering anything about prior work, decisions, dates, people, preferences, or todos: run memory_search against Dory-backed memory first and answer from the matching results.";
            }
            else {
                toolGuidance =
                    "Before answering anything about prior work, decisions, dates, people, preferences, or todos that already point to a specific memory note: run memory_get to pull only the needed lines.";
            }
            const lines = ["## Memory Recall", toolGuidance];
            if (hasMemoryWrite) {
                lines.push("When the user explicitly asks you to remember, save, or log durable information for later recall, use memory_write to store a concise note in Dory-backed memory.");
            }
            if (citationsMode === "off") {
                lines.push("Citations are disabled: do not mention file paths or line numbers in replies unless the user explicitly asks.");
            }
            else {
                lines.push("Citations: include Source: <path#line> when it helps the user verify memory snippets.");
            }
            lines.push("");
            return lines;
        },
        runtime: {
            getMemorySearchManager: async ({ agentId, purpose }) => {
                const key = `${purpose ?? "default"}:${agentId}`;
                let manager = managerCache.get(key);
                if (!manager) {
                    manager = new DoryMemorySearchManager(options, agentId);
                    managerCache.set(key, manager);
                }
                if (purpose === "status") {
                    await manager.refreshStatus().catch(() => { });
                }
                return { manager };
            },
            resolveMemoryBackendConfig: ({ agentId }) => ({
                backend: "qmd",
                qmd: {
                    provider: "dory-http",
                    pluginId: PLUGIN_ID,
                    agentId,
                    baseUrl: options.baseUrl,
                    tokenConfigured: Boolean(options.token),
                },
            }),
            closeAllMemorySearchManagers: async () => {
                for (const manager of managerCache.values()) {
                    await manager.close?.();
                }
                managerCache.clear();
            },
        },
        flushPlanResolver: () => ({
            softThresholdTokens: 4000,
            forceFlushTranscriptBytes: 24000,
            reserveTokensFloor: 1200,
            prompt: "Summarize durable memory worth saving before compaction. Use semantic memory_write actions only for facts, preferences, project state, decisions, or notes the user would want recalled later.",
            systemPrompt: "You are preparing durable memory for Dory. Do not choose markdown paths. Use memory_write with semantic subjects and concise content only.",
            relativePath: "openclaw/compaction-flush.md",
        }),
        publicArtifacts: {
            listArtifacts: async () => {
                const payload = await getPublicArtifacts(options);
                return Array.isArray(payload.artifacts) ? payload.artifacts : [];
            },
        },
    };
}
async function emitRecallEvent(options, body) {
    await recordRecallEvent(options, body).catch(() => undefined);
}
function registerMemoryCapabilityCompat(registerMemoryCapability, capability, pluginId = PLUGIN_ID) {
    if (registerMemoryCapability.length >= 2) {
        registerMemoryCapability(pluginId, capability);
        return capability;
    }
    registerMemoryCapability(capability);
    return capability;
}
function resolveClientOptions(pluginConfig) {
    const baseUrl = String(pluginConfig?.baseUrl ?? pluginConfig?.base_url ?? "").trim();
    if (!baseUrl) {
        throw new Error("dory-memory plugin requires plugins.entries.dory-memory.config.baseUrl");
    }
    const token = typeof pluginConfig?.token === "string" && pluginConfig.token.trim()
        ? pluginConfig.token.trim()
        : undefined;
    return { baseUrl, token };
}
function mapSearchResult(item) {
    const [startLine, endLine] = parseLineSpan(item.lines);
    const path = String(item.path ?? "");
    return {
        path,
        startLine,
        endLine,
        score: Number(item.score ?? 0),
        snippet: String(item.snippet ?? ""),
        source: path.startsWith("logs/sessions/") ? "sessions" : "memory",
        citation: path ? `${path}:${startLine}` : undefined,
    };
}
function parseLineSpan(lines) {
    if (Array.isArray(lines)) {
        const startLine = Number(lines[0] ?? 1);
        const endLine = Number(lines.length > 1 ? lines[1] : startLine);
        return [startLine, endLine];
    }
    if (typeof lines === "string") {
        const trimmed = lines.trim();
        if (!trimmed) {
            return [1, 1];
        }
        const [rawStart, rawEnd] = trimmed.split("-", 2);
        const startLine = Number(rawStart || 1);
        const endLine = Number(rawEnd || rawStart || 1);
        return [
            Number.isFinite(startLine) && startLine > 0 ? startLine : 1,
            Number.isFinite(endLine) && endLine > 0 ? endLine : (Number.isFinite(startLine) && startLine > 0 ? startLine : 1),
        ];
    }
    return [1, 1];
}
const pluginEntry = definePluginEntry({
    id: PLUGIN_ID,
    name: "Dory Memory",
    description: "Dory-backed OpenClaw memory slot plugin.",
    kind: "memory",
    configSchema: CONFIG_SCHEMA,
    register(api) {
        const options = resolveClientOptions(api.pluginConfig);
        const capability = buildDoryMemoryCapability(options);
        registerMemoryCapabilityCompat(api.registerMemoryCapability, capability, PLUGIN_ID);
        api.registerTool?.((ctx) => createMemorySearchTool(options, ctx), {
            names: ["memory_search"],
        });
        api.registerTool?.((ctx) => createMemoryGetTool(options, ctx), {
            names: ["memory_get"],
        });
        api.registerTool?.((ctx) => createMemoryWriteTool(options, ctx), {
            names: ["memory_write"],
        });
    },
});
export default pluginEntry;
