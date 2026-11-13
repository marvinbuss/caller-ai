"""Browser-side JavaScript for the live audio bridge: capture worklet, budget and test engine.

The module holds JavaScript as strings that Reflex injects into the page. `WORKLET_CODE` runs in
the audio thread and moves 24 kHz PCM16 between the microphone, the speaker and the engine in
bounded packets, ordered by generation, capture epoch and sequence numbers so stale audio is
dropped after a clear or mute. `BUDGET_CODE` bounds the samples and bytes in flight in either
direction, `ENGINE_CODE` is the test engine that owns the socket, devices and status line, and
`HOOK_CODE` wires it to the page controls. Content and media stay in bounded browser memory.
"""

import json

WORKLET_CODE = """
class CallerPCM extends AudioWorkletProcessor {
    constructor() {
        super();
        if (sampleRate !== 24000) throw Error("unsupported_audio_rate");
        this.generation = 1; this.alive = true; this.muted = false;
        this.captureEpoch = 1; this.clearSequence = 0;
        this.capture = null; this.captureOffset = 0; this.grants = 0;
        this.playback = []; this.playbackBytes = 0; this.playbackOffset = 0; this.lastSequence = 0;
        this.primed = false; this.waited = 0;
        this.port.onmessage = event => {
            const value = event.data;
            try {
                if (!this.alive || !value || !Number.isInteger(value.generation) ||
                    value.generation < this.generation)
                    return;
                if (["clear", "mute", "grant"].includes(value.type)) {
                    if (!Number.isInteger(value.captureEpoch) || value.captureEpoch < 1 ||
                        value.captureEpoch > 4294967295) throw Error("invalid_capture_epoch");
                    if (value.generation === this.generation &&
                        value.captureEpoch < this.captureEpoch) return;
                }
                if (value.type === "clear") {
                    if (!Number.isInteger(value.clearSequence) || value.clearSequence < 1 ||
                        value.clearSequence > 4294967295) throw Error("invalid_clear_sequence");
                    if (value.clearSequence <= this.clearSequence) return;
                    this.clearSequence = value.clearSequence;
                    this.captureEpoch = value.captureEpoch;
                    this.flush(); this.generation = value.generation;
                    this.port.postMessage({type: "cleared", generation: this.generation,
                        captureEpoch: this.captureEpoch, clearSequence: this.clearSequence});
                } else if (value.generation !== this.generation) {
                    throw Error("invalid_generation");
                } else if (value.type === "mute" && typeof value.muted === "boolean") {
                    this.captureEpoch = value.captureEpoch;
                    this.muted = value.muted; this.capture = null;
                    this.captureOffset = this.grants = 0;
                    this.port.postMessage({type: "capture.flushed", generation: this.generation,
                        captureEpoch: this.captureEpoch});
                } else if (value.type === "grant" && !this.muted &&
                    value.captureEpoch === this.captureEpoch &&
                    this.grants + (this.capture ? 1 : 0) < 2) {
                    this.grants++;
                } else if (value.type === "play") {
                    if (!(value.pcm instanceof ArrayBuffer) || value.pcm.byteLength < 2 ||
                        value.pcm.byteLength > 1920 ||
                        value.pcm.byteLength % 2 || value.sequence !== this.lastSequence + 1 ||
                        this.playbackBytes + value.pcm.byteLength > 24000)
                        throw Error("invalid_playback");
                    this.lastSequence = value.sequence;
                    this.playback.push(value); this.playbackBytes += value.pcm.byteLength;
                } else {
                    throw Error("invalid_worklet_control");
                }
            } catch {
                this.alive = false; this.flush(); this.port.postMessage({type: "failed"});
            }
        };
    }
    flush() {
        this.capture = null; this.captureOffset = this.grants = 0;
        this.playback = []; this.playbackBytes = this.playbackOffset = this.lastSequence = 0;
        this.primed = false; this.waited = 0;
    }
    process(inputs, outputs) {
        const output = outputs[0]?.[0];
        if (output) output.fill(0);
        if (!this.alive) return false;
        const input = inputs[0]?.[0];
        if (input && !this.muted) {
            for (const value of input) {
                if (this.capture === null) {
                    if (!this.grants) break;
                    this.grants--; this.capture = new ArrayBuffer(960); this.captureOffset = 0;
                }
                if (!Number.isFinite(value)) {
                    this.alive = false; this.flush();
                    this.port.postMessage({type: "failed"}); return false;
                }
                const bounded = Math.max(-1, Math.min(1, value));
                new DataView(this.capture).setInt16(this.captureOffset++ * 2,
                    Math.max(-32768, Math.min(32767, Math.round(bounded * 32768))), true);
                if (this.captureOffset === 480) {
                    const pcm = this.capture;
                    this.capture = null; this.captureOffset = 0;
                    this.port.postMessage({type: "capture", generation: this.generation,
                        captureEpoch: this.captureEpoch, pcm}, [pcm]);
                }
            }
        }
        if (output) {
            // Live arrives near real time; start or resume only with 200 ms queued, or after
            // waiting 200 ms so a short final phrase still plays.
            if (!this.primed && this.playback.length) {
                this.waited += output.length;
                this.primed = this.playbackBytes >= 9600 || this.waited >= 4800;
            }
            for (let index = 0; this.primed && index < output.length &&
                this.playback.length; index++) {
                const packet = this.playback[0], view = new DataView(packet.pcm);
                output[index] = view.getInt16(this.playbackOffset, true) / 32768;
                this.playbackOffset += 2;
                if (this.playbackOffset === packet.pcm.byteLength) {
                    this.playback.shift(); this.playbackBytes -= packet.pcm.byteLength;
                    this.playbackOffset = 0;
                    this.port.postMessage({type: "played", generation: packet.generation,
                        sequence: packet.sequence});
                }
            }
            if (this.primed && !this.playback.length) { this.primed = false; this.waited = 0; }
        }
        return true;
    }
}
registerProcessor("caller-pcm", CallerPCM);
"""

BUDGET_CODE = """
class CallerAudioBudget {
    constructor() { this.reset(1); }
    reset(generation) {
        if (!Number.isInteger(generation) || generation < 1 || generation > 4294967295)
            throw Error("invalid_generation");
        this.generation = generation;
        this.inputSequence = this.outputSequence = this.playedSequence = this.acceptedSequence = 0;
        this.input = new Map(); this.output = new Map();
        this.available = this.reserved = 0;
        this.captureEpoch = 1; this.captureReady = true;
        this.blocked = this.muted = false;
    }
    get captureSamples() {
        return this.reserved + [...this.input.values()].reduce((total, count) => total + count, 0);
    }
    get playbackBytes() {
        return [...this.output.values()].reduce((total, count) => total + count, 0);
    }
    credit(samples, accepted) {
        if (!Number.isInteger(samples) || samples < 0 || samples > 6000 ||
            !Number.isInteger(accepted) || accepted < this.acceptedSequence ||
            accepted > this.inputSequence)
            throw Error("invalid_audio_credit");
        this.acceptedSequence = accepted;
        for (const sequence of this.input.keys())
            if (sequence <= accepted) this.input.delete(sequence);
        this.available = samples;
    }
"""

CONTROL_CODE = r"""
    message(raw, epoch) {
        if (epoch !== this.epoch && !this.terminal) return;
        if (raw instanceof ArrayBuffer) {
            if (this.terminal) return;
            if (this.mode !== "speech" || this.snapshot.status !== "ready" || !this.node)
                throw Error("invalid_audio_mode");
            const packet = this.budget.receive(raw);
            if (packet) {
                this.node.port.postMessage({type: "play", ...packet}, [packet.pcm]);
                this.playing(true);
            }
            return;
        }
        // Only a `text.formatted` tree may exceed the 8 KiB control limit, up to 192 KiB.
        const size = typeof raw === "string" ? new TextEncoder().encode(raw).length : Infinity;
        if (size > 196608) throw Error("control_limit");
        const value = JSON.parse(raw), identifier = text => typeof text === "string" &&
            /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(text);
        if (size > 8192 && value?.type !== "text.formatted") throw Error("control_limit");
        const integer = number => Number.isInteger(number) && number >= 1 && number <= 4294967295;
        if (!value || Array.isArray(value) || !integer(value.sequence) ||
            !integer(value.generation) || !identifier(value.sessionId))
            throw Error("invalid_control");
        if (value.type === "ready") {
            if (this.terminal) return;
            const expected = ["type", "sequence", "generation", "sessionId", "mode",
                "revisionId", "revisionNumber", "agentId", "expiresAt", "idleExpiresAt",
                "bounds", ...(this.mode === "speech" ? ["audioFormat"] : [])];
            const bounds = {controlBytes: 8192, textBytes: 4096, replyBytes: 65536,
                captureBytes: 12000, playbackBytes: 24000, inputFrameBytes: 1920};
            const expiry = Date.parse(value.expiresAt), idle = Date.parse(value.idleExpiresAt);
            // The relay sets text expiry to its clock plus exactly 10 minutes; allow 30 s of skew.
            if (this.snapshot.status !== "starting" || performance.now() >= this.deadline ||
                value.sequence !== 1 || value.mode !== this.mode ||
                Object.keys(value).length !== expected.length ||
                expected.some(key => !(key in value)) || !identifier(value.revisionId) ||
                !identifier(value.agentId) || !integer(value.revisionNumber) ||
                !value.bounds || Object.keys(value.bounds).length !== 6 ||
                Object.entries(bounds).some(([key, count]) => value.bounds[key] !== count) ||
                !Number.isFinite(expiry) || !Number.isFinite(idle) || expiry <= Date.now() ||
                expiry - Date.now() > 630000 || idle > expiry)
                throw Error("invalid_ready");
            if (this.mode === "speech" && (!value.audioFormat ||
                Object.keys(value.audioFormat).length !== 3 ||
                value.audioFormat.codec !== "pcm16le" ||
                value.audioFormat.sampleRate !== 24000 || value.audioFormat.channels !== 1))
                throw Error("invalid_audio_format");
            clearTimeout(this.startTimer); this.serverSequence = 1;
            this.snapshot = Object.fromEntries(["sessionId", "revisionId", "revisionNumber",
                "agentId", "expiresAt", "idleExpiresAt", "generation"]
                .map(key => [key, value[key]]));
            this.budget.reset(value.generation); this.report("ready", null, "active");
            this.write("revision", "R" + value.revisionNumber + " / " + value.revisionId);
            this.write("agent", value.agentId); this.write("community", "Global");
            if (this.node) {
                this.clearWorklet();
                this.grant(); this.write("capture", "Active");
            }
            this.clock = {utc: Date.now(), tick: performance.now()}; this.watch(); return;
        }
        const fields = {
            "text.delta": ["messageId", "text"], "text.completed": ["messageId", "text"],
            "text.formatted": ["messageId", "tree"],
            "progress": this.mode === "text" ? ["messageId", "state", "text"] : ["state", "text"],
            "transcript.delta": ["speaker", "fragment", "start_ms", "end_ms"],
            "agent.changed": ["agentId", "communityId"], "generation.changed": ["reason"],
            "audio.credit": ["availableSamples", "acceptedAudioSequence"],
            "command.ack": ["acceptedSequence"], "ping": ["nonce"], "pong": ["nonce"],
            "error": ["code", "cleanupStatus", "correlationId"],
            "ended": ["code", "cleanupStatus", "correlationId"]
        };
        const payload = fields[value.type], expected = ["type", "sequence", "generation",
            "sessionId", ...(payload || []),
            ...(value.type === "error" && "messageId" in value ? ["messageId"] : []),
            ...(value.type === "error" && "reason" in value ? ["reason"] : [])];
        if (!payload || value.sessionId !== this.snapshot.sessionId ||
            value.sequence <= this.serverSequence ||
            Object.keys(value).length !== expected.length ||
            expected.some(key => !(key in value))) throw Error("invalid_control");
        if (value.type === "error" && "messageId" in value && !identifier(value.messageId))
            throw Error("invalid_work_identifier");
        if ("reason" in value && !(typeof value.reason === "string" &&
            /^[a-z][a-z_]{0,63}$/.test(value.reason))) throw Error("invalid_reason");
        this.serverSequence = value.sequence;
        if (this.terminal && !["ended", "error"].includes(value.type)) return;
        if (value.generation < this.snapshot.generation) return;
        if (value.generation > this.snapshot.generation) {
            if (!["generation.changed", "agent.changed", "error", "ended"].includes(value.type))
                throw Error("invalid_generation");
            const muted = this.budget.muted;
            this.budget.reset(value.generation); this.budget.mute(muted);
            this.snapshot.generation = value.generation;
            this.queue = this.queue.filter(entry => typeof entry.value === "string");
            if (this.node) {
                this.gain.gain.value = 0;
                this.clearWorklet();
                this.node.port.postMessage({type: "mute", generation: value.generation,
                    captureEpoch: this.budget.captureEpoch, muted});
            }
        }
        if (["error", "ended"].includes(value.type)) {
            // The backend's public error codes (docs/conversation-endpoints.md), shown unchanged.
            const codes = {stopped: "stopped", unauthorized: value.reason === "session_expired" ?
                "lifetime_expired" : "permission_denied", forbidden: "permission_denied",
                settings_not_supported: "invalid_input", limit_exceeded: "invalid_input",
                capacity_unavailable: value.reason === "manager_busy" ? "manager_busy" :
                "unavailable", invalid_request: "invalid_input",
                upstream_unavailable: "unavailable", internal_error: "unavailable"};
            if (!(value.code in codes) || (value.type === "ended") !== (value.code === "stopped") ||
                !["active", "complete", "pending"].includes(value.cleanupStatus) ||
                !identifier(value.correlationId))
                throw Error("invalid_terminal");
            if (value.type === "error" && value.cleanupStatus === "active" && !this.terminal) {
                this.pending.delete(this.pending.keys().next().value);
                this.write("error", value.code === "capacity_unavailable" ? "Work busy" :
                    "Work unavailable");
                return;
            }
            this.finish(codes[value.code], value.cleanupStatus); return;
        }
        if (value.type === "audio.credit") {
            if (this.mode !== "speech") throw Error("invalid_audio_mode");
            this.budget.credit(value.availableSamples, value.acceptedAudioSequence); this.grant();
        } else if (["text.delta", "text.completed"].includes(value.type)) {
            if (this.mode !== "text" || !identifier(value.messageId) ||
                typeof value.text !== "string" ||
                new TextEncoder().encode(value.text).length > 4096) throw Error("invalid_text");
            this.append("assistant", value.text, value.messageId);
            if (value.type === "text.completed") {
                const row = this.reply.get(value.messageId);
                if (row) {
                    // Keep the last two completed rows so a later `text.formatted` can replace one.
                    this.completed.set(value.messageId, row);
                    if (this.completed.size > 2)
                        this.completed.delete(this.completed.keys().next().value);
                }
                this.reply.delete(value.messageId);
                this.replySizes.delete(value.messageId);
                this.pending.delete(this.pending.keys().next().value);
            }
        } else if (value.type === "text.formatted") {
            if (this.mode !== "text" || !identifier(value.messageId))
                throw Error("invalid_text");
            const row = this.completed.get(value.messageId);
            this.completed.delete(value.messageId);
            if (row) this.format(row, value.tree);
        } else if (value.type === "progress") {
            const states = ["still_working", "waiting_for_input", "completed", "failed",
                "cancellation_confirmed"];
            if (!states.includes(value.state) || typeof value.text !== "string" ||
                [...value.text].length < 1 || [...value.text].length > 200 ||
                this.mode === "text" && !identifier(value.messageId))
                throw Error("invalid_progress");
            this.status(value.text);
        } else if (value.type === "transcript.delta") {
            if (this.mode !== "speech" || !["user", "assistant"].includes(value.speaker) ||
                typeof value.fragment !== "string" ||
                new TextEncoder().encode(value.fragment).length > 4096 ||
                !Number.isFinite(value.start_ms) || !Number.isFinite(value.end_ms) ||
                value.start_ms < 0 || value.end_ms < value.start_ms || value.end_ms > 3600000)
                throw Error("invalid_transcript");
            this.append(value.speaker, value.fragment);
        } else if (value.type === "agent.changed") {
            if (!identifier(value.agentId) ||
                (value.communityId !== null && !identifier(value.communityId)))
                throw Error("invalid_agent");
            this.snapshot.agentId = value.agentId; this.snapshot.communityId = value.communityId;
            this.write("agent", value.agentId);
            this.write("community", value.communityId || "Global");
            this.report("ready", null, "active");
        } else if (value.type === "generation.changed") {
            if (!["segment_retired", "agent_changed", "interrupted", "stopped"]
                .includes(value.reason)) throw Error("invalid_generation");
            this.report("ready", null, "active");
        } else if (["ping", "pong"].includes(value.type)) {
            if (typeof value.nonce !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(value.nonce))
                throw Error("invalid_nonce");
            if (value.type === "ping") this.command("pong", {nonce: value.nonce});
        } else if (value.type === "command.ack" && (!integer(value.acceptedSequence) ||
            value.acceptedSequence > this.clientSequence)) {
            throw Error("invalid_ack");
        }
    }
    watch() {
        if (this.terminal) return;
        const utc = Date.now(), tick = performance.now();
        if (utc < this.clock.utc || tick < this.clock.tick ||
            Math.abs(utc - this.clock.utc - (tick - this.clock.tick)) > 1000 ||
            utc + 1000 >= Date.parse(this.snapshot.expiresAt)) {
            this.stop("lifetime_expired"); return;
        }
        this.clock = {utc, tick}; this.watchTimer = setTimeout(() => this.watch(), 900);
    }
    append(speaker, text, identifier = null) {
        const count = new TextEncoder().encode(text).length;
        if (identifier) {
            if (!this.replySizes.has(identifier) && this.replySizes.size >= 2)
                throw Error("reply_queue_limit");
            const size = (this.replySizes.get(identifier) || 0) + count;
            if (size > 65536) throw Error("reply_content_limit");
            this.replySizes.set(identifier, size);
        }
        let row = identifier ? this.reply.get(identifier) :
            this.mode === "speech" && this.fragmentSpeaker === speaker ? this.fragmentRow : null;
        const prefix = speaker === "user" ? "You: " : "Agent: ";
        const overhead = row ? 0 : prefix.length;
        if (this.contentBytes + count + overhead > 262144 ||
            this.mode === "speech" && this.transcriptBytes + count + overhead > 65536 ||
            !row && this.rowCount >= 128) throw Error("volatile_content_limit");
        this.contentBytes += count + overhead;
        if (this.mode === "speech") this.transcriptBytes += count + overhead;
        if (!row) this.rowCount++;
        const container = this.element("messages");
        if (!row && container) {
            row = document.createElement("p"); row.dataset.speaker = speaker;
            row.style.whiteSpace = "pre-wrap"; row.style.overflowWrap = "anywhere";
            row.textContent = prefix; container.append(row);
            if (identifier) this.reply.set(identifier, row);
        }
        if (this.mode === "speech") {
            this.fragmentSpeaker = speaker; this.fragmentRow = row;
        }
        if (row) row.textContent += text;
        if (container) container.scrollTop = container.scrollHeight;
    }
    status(text) {
        // A progress update: its own row with a visible prefix, kept in the log (FR-041).
        const count = new TextEncoder().encode(text).length + 8;
        if (this.contentBytes + count > 262144 || this.rowCount >= 128)
            throw Error("volatile_content_limit");
        this.contentBytes += count; this.rowCount++;
        // The next caption starts a new row below the status.
        this.fragmentRow = this.fragmentSpeaker = null;
        const container = this.element("messages");
        if (!container) return;
        const row = document.createElement("p"); row.dataset.kind = "status";
        row.style.whiteSpace = "pre-wrap"; row.style.overflowWrap = "anywhere";
        row.textContent = "Status: " + text; container.append(row);
        container.scrollTop = container.scrollHeight;
    }
    format(row, tree) {
        // Builds the allowlisted tree with createElement and textContent only. Links and images
        // keep their URL only when it is https:. Anything unexpected keeps the plain-text row.
        const tags = new Set(["p", "h1", "h2", "h3", "ul", "ol", "li", "blockquote", "pre",
            "code", "table", "thead", "tbody", "tr", "th", "td", "strong", "em", "br", "hr",
            "a", "img"]);
        const keys = {a: ["type", "href", "children"], img: ["type", "src", "alt"],
            br: ["type"], hr: ["type"]};
        let count = 0;
        const https = url => {
            if (typeof url !== "string") throw Error("invalid_format");
            try {
                const parsed = new URL(url);
                return parsed.protocol === "https:" ? parsed.href : null;
            } catch { return null; }
        };
        const text = value => {
            const span = document.createElement("span"); span.textContent = value; return span;
        };
        const build = (node, depth) => {
            if (++count > 2000 || depth > 12) throw Error("format_limit");
            if (typeof node === "string") return text(node);
            if (!node || typeof node !== "object" || Array.isArray(node) ||
                !tags.has(node.type)) throw Error("invalid_format");
            const expected = keys[node.type] || ["type", "children"];
            if (Object.keys(node).length !== expected.length ||
                expected.some(key => !(key in node))) throw Error("invalid_format");
            if (node.type === "img") {
                if (typeof node.alt !== "string") throw Error("invalid_format");
                const src = https(node.src);
                if (!src) return text(node.alt);
                const image = document.createElement("img");
                image.src = src; image.alt = node.alt; image.referrerPolicy = "no-referrer";
                image.loading = "lazy"; image.style.maxWidth = "100%";
                return image;
            }
            if (!("children" in node)) return document.createElement(node.type);
            if (!Array.isArray(node.children)) throw Error("invalid_format");
            const children = node.children.map(child => build(child, depth + 1));
            let element;
            if (node.type === "a") {
                const href = https(node.href);
                element = document.createElement(href ? "a" : "span");
                if (href) {
                    element.href = href; element.target = "_blank";
                    element.rel = "noopener noreferrer"; element.referrerPolicy = "no-referrer";
                }
            } else element = document.createElement(node.type);
            element.append(...children);
            return element;
        };
        let children;
        try {
            if (!tree || typeof tree !== "object" || Array.isArray(tree) ||
                tree.type !== "root" || Object.keys(tree).length !== 2 ||
                !Array.isArray(tree.children)) return;
            children = tree.children.map(child => build(child, 1));
        } catch { return; }
        const formatted = document.createElement("div");
        formatted.dataset.speaker = row.dataset.speaker; formatted.dataset.formatted = "true";
        formatted.style.overflowWrap = "anywhere";
        formatted.append(text("Agent: "), ...children);
        row.replaceWith(formatted);
    }
    submit(text) {
        if (this.terminal || this.mode !== "text" || this.snapshot.status !== "ready") return;
        if (typeof text !== "string" || !text.trim() ||
            new TextEncoder().encode(text).length > 4096) {
            this.write("error", "Invalid input"); return;
        }
        if (this.pending.size >= 2) { this.write("error", "Work busy"); return; }
        try {
            const sequence = this.command("text.submit", {text}); this.pending.set(sequence, true);
            this.append("user", text);
            const input = this.element("text"); if (input) input.value = "";
        } catch { this.stop("protocol_error"); }
    }
    mute() {
        if (this.terminal || this.mode !== "speech" || !this.node) return;
        if (this.socket?.bufferedAmount) { this.stop("unavailable"); return; }
        const muted = !this.budget.muted;
        this.budget.mute(muted);
        this.queue = this.queue.filter(entry => typeof entry.value === "string");
        for (const track of this.stream.getTracks()) track.enabled = !muted;
        this.node.port.postMessage({type: "mute", generation: this.budget.generation,
            captureEpoch: this.budget.captureEpoch, muted});
        this.command("input.mute", {muted}); this.write("capture", muted ? "Muted" : "Active");
        const button = this.element("mute");
        if (button) button.setAttribute("aria-pressed", String(muted));
        this.grant();
    }
    interrupt() {
        if (this.terminal || this.mode !== "speech" || !this.node) return;
        clearTimeout(this.idleTimer);
        this.gain.gain.value = 0; this.budget.interrupt(); this.budget.reserved = 0;
        this.queue = this.queue.filter(entry => typeof entry.value === "string");
        this.clearWorklet();
        this.command("output.interrupt", {generation: this.budget.generation});
        this.write("playback", "Interrupted");
    }
"""

BUDGET_CODE += """
    grant() {
        if (this.muted || this.blocked || !this.captureReady || this.reserved >= 960 ||
            this.captureSamples + 480 > 6000 ||
            this.captureSamples + 480 > this.available) return false;
        this.reserved += 480;
        return true;
    }
    capture(pcm, generation, captureEpoch) {
        if (generation !== this.generation || captureEpoch !== this.captureEpoch ||
            this.muted || this.blocked || !this.captureReady) return null;
        if (!(pcm instanceof ArrayBuffer) || pcm.byteLength !== 960 || this.reserved < 480)
            throw Error("invalid_capture_frame");
        this.reserved -= 480;
        if (this.inputSequence === 4294967295) throw Error("audio_sequence_exhausted");
        const sequence = ++this.inputSequence, raw = new ArrayBuffer(969), view = new DataView(raw);
        view.setUint8(0, 1); view.setUint32(1, generation); view.setUint32(5, sequence);
        new Uint8Array(raw, 9).set(new Uint8Array(pcm));
        this.input.set(sequence, 480);
        return raw;
    }
    receive(raw) {
        if (!(raw instanceof ArrayBuffer) || raw.byteLength < 11 || raw.byteLength > 1929 ||
            (raw.byteLength - 9) % 2) throw Error("invalid_playback_frame");
        const view = new DataView(raw), generation = view.getUint32(1);
        const sequence = view.getUint32(5);
        if (view.getUint8(0) !== 1 || !generation || !sequence) throw Error("invalid_audio_header");
        if (generation < this.generation || (generation === this.generation && this.blocked))
            return null;
        if (generation !== this.generation || sequence !== this.outputSequence + 1)
            throw Error("invalid_playback_sequence");
        const count = raw.byteLength - 9;
        if (this.playbackBytes + count > 24000) throw Error("playback_limit");
        this.outputSequence = sequence;
        this.output.set(sequence, count);
        return {generation, sequence, pcm: raw.slice(9)};
    }
    played(sequence, generation) {
        if (generation !== this.generation || this.blocked) return false;
        if (!Number.isInteger(sequence) || sequence < this.playedSequence ||
            sequence > this.outputSequence)
            throw Error("invalid_played_sequence");
        this.playedSequence = sequence;
        for (const current of this.output.keys())
            if (current <= sequence) this.output.delete(current);
        return true;
    }
    mute(value) {
        if (typeof value !== "boolean") throw Error("invalid_mute");
        this.muted = value; this.flushCapture();
    }
    flushCapture() {
        if (this.captureEpoch === 4294967295) throw Error("capture_epoch_exhausted");
        this.captureEpoch++; this.reserved = 0; this.captureReady = false;
    }
    flushed(captureEpoch) {
        if (captureEpoch !== this.captureEpoch) return false;
        this.captureReady = true; return true;
    }
    interrupt() { this.blocked = true; this.output.clear(); this.flushCapture(); }
    clear() { this.interrupt(); this.mute(true); this.input.clear(); this.available = 0; }
}
"""

WORKLET_SOURCE_CODE = "const CALLER_WORKLET_SOURCE = " + json.dumps(WORKLET_CODE) + ";"

ENGINE_CODE = (
    r"""
class CallerTestEngine {
    constructor(events) {
        this.events = events; this.budget = new CallerAudioBudget(); this.epoch = 0;
        this.terminal = true; this.mode = "text"; this.snapshot = {};
        this.context = this.stream = this.node = this.source = this.gain = this.socket = null;
        this.queue = []; this.pending = new Map(); this.reply = new Map(); this.contentBytes = 0;
        this.flushSequence = 0; this.completed = new Map();
        this.replySizes = new Map(); this.transcriptBytes = this.rowCount = 0;
        this.fragmentRow = this.fragmentSpeaker = null;
        this.clientSequence = this.serverSequence = 0; this.authenticated = false;
        this.playbackUnavailable = false;
    }
    element(name) { return this.detached ? null : document.getElementById("test-" + name); }
    write(name, value) {
        const element = this.element(name); if (element) element.textContent = value;
    }
    report(status, code = null, cleanupStatus = null) {
        this.snapshot = {...this.snapshot, status, mode: this.mode, code, cleanupStatus};
        this.write("status", status.replaceAll("_", " "));
        const errors = {permission_denied: "Permission denied", unavailable: "Unavailable",
            invalid_input: "Invalid input", quota: "Quota unavailable",
            manager_busy: "Another test is active or an earlier test is still being cleaned up",
            protocol_error: "Invalid protocol",
            lifetime_expired: "Test expired", idle_expired: "Idle expired",
            auth_expired: "Permission expired"};
        this.write("error", this.playbackUnavailable ? "Playback unavailable" : errors[code] || "");
        this.events.control(JSON.stringify(this.snapshot));
        const active = ["permission", "starting", "ready", "stopping", "cleanup_pending"]
            .includes(status);
        for (const name of ["start", "mode", "input-device", "output-device"]) {
            const element = this.element(name); if (element) element.disabled = active;
        }
        const output = this.element("output-device");
        if (output && typeof window.AudioContext?.prototype?.setSinkId !== "function")
            output.disabled = true;
        const stop = this.element("stop"); if (stop) stop.disabled = this.terminal;
        const send = this.element("send");
        if (send) send.disabled = status !== "ready" || this.mode !== "text";
        for (const name of ["mute", "interrupt"]) {
            const element = this.element(name);
            if (element) element.disabled = status !== "ready" || this.mode !== "speech";
        }
    }
    current(epoch) { return !this.terminal && epoch === this.epoch; }
    playing(active) {
        clearTimeout(this.idleTimer);
        if (active) { this.write("playback", "Playing"); return; }
        // Gaps between Live packets are not idle; only a second without audio is.
        this.idleTimer = setTimeout(() => {
            if (!this.terminal && !this.budget.playbackBytes) this.write("playback", "Idle");
        }, 1000);
    }
    async readJSON(response) {
        if (!response.ok) throw Error("test_unavailable");
        const reader = response.body.getReader(), parts = []; let count = 0;
        try {
            while (true) {
                const value = await reader.read(); if (value.done) break;
                count += value.value.byteLength;
                if (count > 8192) throw Error("control_limit");
                parts.push(value.value);
            }
        } finally { reader.releaseLock(); }
        const bytes = new Uint8Array(count); let offset = 0;
        for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
        const value = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
        if (!value || typeof value !== "object" || Array.isArray(value))
            throw Error("invalid_control");
        return value;
    }
    async media(epoch, inputDevice, outputDevice) {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia ||
            !window.AudioContext)
            throw Error("audio_unavailable");
        const context = new window.AudioContext({sampleRate: 24000, latencyHint: "interactive"});
        this.context = context;
        if (context.sampleRate !== 24000) throw Error("unsupported_audio_rate");
        const resumed = context.resume().then(() => true, () => false);
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true,
                ...(inputDevice ? {deviceId: {exact: inputDevice}} : {})}, video: false
        });
        if (!this.current(epoch)) { for (const track of stream.getTracks()) track.stop(); return; }
        this.stream = stream;
        for (const track of stream.getTracks()) track.onended = () => this.stop("unavailable");
        const playbackReady = await resumed;
        if (!this.current(epoch)) return;
        if (!playbackReady || context.state !== "running" || !context.audioWorklet)
            throw Error("playback_unavailable");
        context.onstatechange = () => {
            if (this.context === context && this.current(epoch) && context.state !== "running") {
                this.playbackUnavailable = true; this.stop("unavailable");
            }
        };
        if (outputDevice) {
            if (typeof context.setSinkId !== "function") throw Error("output_unavailable");
            await context.setSinkId(outputDevice);
            if (context.sinkId !== outputDevice) throw Error("output_unavailable");
        }
        if (!this.current(epoch)) return;
        const module = URL.createObjectURL(new Blob([CALLER_WORKLET_SOURCE],
            {type: "text/javascript"}));
        try { await context.audioWorklet.addModule(module); }
        finally { URL.revokeObjectURL(module); }
        if (!this.current(epoch)) return;
        this.node = new AudioWorkletNode(context, "caller-pcm", {
            numberOfInputs: 1, numberOfOutputs: 1,
            outputChannelCount: [1], channelCount: 1, channelCountMode: "explicit"});
        this.source = context.createMediaStreamSource(stream); this.gain = context.createGain();
        this.gain.gain.value = 0;
        this.source.connect(this.node); this.node.connect(this.gain);
        this.gain.connect(context.destination);
        this.node.onprocessorerror = () => this.stop("unavailable");
        this.node.port.onmessage = event => {
            if (!this.current(epoch) || this.snapshot.status !== "ready") return;
            try {
                const value = event.data;
                if (value.type === "failed") throw Error("audio_unavailable");
                if (value.generation !== this.budget.generation) return;
                if (value.type === "capture") {
                    const frame = this.budget.capture(value.pcm, value.generation,
                        value.captureEpoch);
                    if (frame) this.enqueue(frame);
                    this.grant();
                } else if (value.type === "capture.flushed") {
                    if (this.budget.flushed(value.captureEpoch)) this.grant();
                } else if (value.type === "played" &&
                    this.budget.played(value.sequence, value.generation)) {
                    this.command("audio.played", {generation: value.generation,
                        audioSequence: value.sequence});
                    this.playing(this.budget.playbackBytes > 0);
                } else if (value.type === "cleared" &&
                    value.clearSequence === this.flushSequence) {
                    if (!this.budget.blocked) this.gain.gain.value = 1;
                    if (this.budget.flushed(value.captureEpoch)) this.grant();
                }
            } catch { this.stop("protocol_error"); }
        };
    }
    async start(mode, inputDevice = "", outputDevice = "") {
        if (!this.terminal || this.snapshot.cleanupStatus === "pending" ||
            !["text", "speech"].includes(mode)) return;
        this.terminal = false; const epoch = ++this.epoch;
        this.mode = mode; this.snapshot = {}; this.authenticated = false;
        this.playbackUnavailable = false;
        this.clientSequence = this.serverSequence = 0;
        this.events.begin(mode); this.report(mode === "speech" ? "permission" : "starting");
        this.abort = new AbortController();
        this.startTimer = setTimeout(() => this.stop("permission_denied"), 10000);
        try {
            if (window.location.protocol !== "https:") throw Error("secure_context_required");
            if (mode === "speech") await this.media(epoch, inputDevice, outputDevice);
            if (!this.current(epoch)) return;
            this.report("starting");
            let identity = await this.readJSON(await fetch("/auth/session", {
                credentials: "same-origin", cache: "no-store", redirect: "error",
                signal: this.abort.signal
            }));
            if (!this.current(epoch)) return;
            if (typeof identity.csrf !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(identity.csrf))
                throw Error("invalid_identity");
            const accepted = performance.now(), requestId = crypto.randomUUID();
            this.deadline = accepted + 10000;
            clearTimeout(this.startTimer);
            this.startTimer = setTimeout(() => this.stop("unavailable"), 10000);
            let ticket = await this.readJSON(await fetch("/v1/console/tickets", {
                method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
                headers: {"Content-Type": "application/json", "X-CSRF-Token": identity.csrf},
                body: JSON.stringify({mode, requestId}), signal: this.abort.signal
            }));
            identity = null;
            if (!this.current(epoch)) { ticket = null; return; }
            const remaining = Math.min(this.deadline - performance.now(),
                Date.parse(ticket.admissionDeadlineUtc) - Date.now() - 1000);
            if (ticket.mode !== mode || ticket.requestId !== requestId ||
                ticket.socketPath !== "/v1/console/sessions" || typeof ticket.permit !== "string" ||
                new TextEncoder().encode(ticket.permit).length > 6144 ||
                !Number.isFinite(remaining) || remaining <= 0 || remaining > 10000)
                throw Error("invalid_ticket");
            clearTimeout(this.startTimer);
            this.deadline = performance.now() + remaining;
            this.startTimer = setTimeout(() => this.stop("unavailable"), remaining);
            const socket = new WebSocket(window.location.origin.replace(/^https:/, "wss:") +
                ticket.socketPath, "caller.console.v1");
            this.socket = socket; socket.binaryType = "arraybuffer";
            socket.onopen = () => {
                if (this.socket !== socket || !this.current(epoch) ||
                    performance.now() >= this.deadline) {
                    ticket = null; socket.close(1000); return;
                }
                const authentication = JSON.stringify({type: "authenticate", requestId,
                    permit: ticket.permit});
                ticket = null;
                if (new TextEncoder().encode(authentication).length > 8192) {
                    this.stop("protocol_error"); return;
                }
                this.authenticated = true; socket.send(authentication);
            };
            socket.onmessage = event => {
                if (this.socket !== socket) return;
                try { this.message(event.data, epoch); }
                catch { this.stop("protocol_error"); }
            };
            socket.onerror = () => {
                if (this.socket === socket && this.current(epoch)) this.stop("unavailable");
            };
            socket.onclose = event => {
                ticket = null;
                if (this.socket !== socket || !this.current(epoch)) return;
                if (!this.terminal && !this.snapshot.sessionId && event.code === 1013 &&
                    event.reason === "manager_busy") {
                    this.finish("manager_busy", "complete"); return;
                }
                if (!this.terminal) this.stop("unavailable");
            };
        } catch (error) {
            if (this.current(epoch)) {
                const denied = error?.name === "NotAllowedError";
                this.stop(denied ? "permission_denied" : "unavailable");
                const labels = {unsupported_audio_rate: "Unsupported audio sample rate",
                    playback_unavailable: "Playback unavailable",
                    output_unavailable: "Output unavailable",
                    audio_unavailable: "Audio unavailable"};
                if (labels[error?.message]) this.write("error", labels[error.message]);
            }
        }
    }
"""
    + CONTROL_CODE
    + r"""
    clearWorklet() {
        if (!this.node) return;
        if (this.flushSequence === 4294967295) throw Error("clear_sequence_exhausted");
        this.budget.captureReady = false; this.budget.reserved = 0;
        this.node.port.postMessage({type: "clear", generation: this.budget.generation,
            captureEpoch: this.budget.captureEpoch, clearSequence: ++this.flushSequence});
    }
    grant() {
        if (this.terminal || this.snapshot.status !== "ready" || !this.node) return;
        while (this.budget.grant()) this.node.port.postMessage({type: "grant",
            generation: this.budget.generation, captureEpoch: this.budget.captureEpoch});
    }
    enqueue(value) {
        if (this.terminal || this.queue.length >= 64) throw Error("control_queue_limit");
        this.queue.push({value, deadline: performance.now() + 1000}); this.drain();
    }
    drain() {
        clearTimeout(this.writerTimer);
        if (this.terminal || !this.socket || this.socket.readyState !== WebSocket.OPEN) return;
        while (this.queue.length) {
            const entry = this.queue[0];
            if (performance.now() >= entry.deadline || this.socket.bufferedAmount > 32768) {
                this.stop("unavailable"); return;
            }
            if (this.socket.bufferedAmount > 12000) {
                this.writerTimer = setTimeout(() => this.drain(), 20); return;
            }
            this.queue.shift(); this.socket.send(entry.value);
        }
    }
    command(type, values = {}, urgent = false) {
        if (this.terminal || !this.snapshot.sessionId || this.clientSequence === 4294967295) return;
        const sequence = ++this.clientSequence;
        const encoded = JSON.stringify({type, sessionId: this.snapshot.sessionId,
            sequence, ...values});
        if (new TextEncoder().encode(encoded).length > 8192) throw Error("control_limit");
        if (urgent) { this.queue = []; this.socket.send(encoded); return sequence; }
        this.enqueue(encoded); return sequence;
    }
    async devices() {
        if (!navigator.mediaDevices?.enumerateDevices) {
            this.write("device-status", "Unavailable"); return;
        }
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            if (devices.length > 64) throw Error("device_limit");
            for (const [name, kind] of [["input-device", "audioinput"],
                ["output-device", "audiooutput"]]) {
                const select = this.element(name); if (!select) continue;
                const selected = select.value;
                select.replaceChildren();
                const fallback = document.createElement("option"); fallback.value = "";
                fallback.textContent = "System default"; select.append(fallback);
                for (const device of devices.filter(value => value.kind === kind)) {
                    const option = document.createElement("option"); option.value = device.deviceId;
                    option.textContent = (device.label ||
                        (kind === "audioinput" ? "Microphone" : "Output")).slice(0, 128);
                    select.append(option);
                }
                if ([...select.options].some(option => option.value === selected))
                    select.value = selected;
            }
            this.write("device-status", "System default");
        } catch { this.write("device-status", "Unavailable"); }
    }
    async chooseOutput() {
        if (!this.terminal || typeof navigator.mediaDevices?.selectAudioOutput !== "function")
            return;
        try {
            const device = await navigator.mediaDevices.selectAudioOutput();
            if (!this.terminal) return;
            await this.devices();
            const select = this.element("output-device");
            if (select) select.value = device.deviceId;
        } catch { this.write("device-status", "System default"); }
    }
    disposeMedia() {
        if (this.context) this.context.onstatechange = null;
        if (this.gain) this.gain.gain.value = 0;
        this.budget.clear();
        if (this.node) {
            this.node.port.onmessage = null;
            this.clearWorklet();
            this.node.disconnect(); this.node.port.close();
        }
        if (this.source) this.source.disconnect();
        if (this.stream) for (const track of this.stream.getTracks()) {
            track.onended = null; track.stop();
        }
        if (this.context) void this.context.close().catch(() => {});
        this.context = this.stream = this.node = this.source = this.gain = null;
        this.write("capture", "Off"); this.write("playback", "Off");
    }
    purge() {
        this.contentBytes = 0; this.pending.clear(); this.reply.clear(); this.queue = [];
        this.completed.clear(); this.replySizes.clear(); this.transcriptBytes = this.rowCount = 0;
        this.fragmentRow = this.fragmentSpeaker = null;
        this.element("messages")?.replaceChildren();
        const input = this.element("text"); if (input) input.value = "";
    }
    stop(code = "stopped") {
        if (this.terminal) return;
        if (this.socket?.readyState === WebSocket.OPEN && this.snapshot.sessionId) {
            // Queued audio must not swallow the stop: it is tiny and leaves right after it.
            this.command("stop", {}, true);
        }
        this.terminal = true; this.epoch++;
        this.abort?.abort();
        for (const timer of [this.startTimer, this.writerTimer, this.watchTimer, this.idleTimer])
            clearTimeout(timer);
        this.disposeMedia(); this.purge();
        if (!this.authenticated || !this.snapshot.sessionId ||
            this.socket?.readyState !== WebSocket.OPEN) {
            this.finish(code, this.authenticated ? "pending" : "complete"); return;
        }
        this.report("stopping", code, "pending");
        this.cleanupTimer = setTimeout(() => this.finish(code, "pending"), 10000);
    }
    finish(code, cleanupStatus) {
        this.terminal = true; this.abort?.abort();
        for (const timer of [this.startTimer, this.writerTimer, this.watchTimer,
            this.cleanupTimer]) clearTimeout(timer);
        this.disposeMedia(); this.purge();
        this.socket?.close(1000, "stopped"); this.socket = null;
        this.report(code === "manager_busy" && !this.snapshot.sessionId ? "busy" :
            cleanupStatus === "complete" ? "ended" : "cleanup_pending",
            code, cleanupStatus);
    }
    dispose() {
        if (!this.terminal) {
            // The frame tells the user on the next page that leaving stopped the test; the flag
            // lives in memory only, so the console keeps nothing in browser storage.
            window.__callerTestLeft = true;
        }
        this.stop(); this.detached = true;
        this.events = {begin: () => {}, control: () => {}};
    }
}
"""
)

HOOK_CODE = """
function CallerLiveAudio({authorized, onBegin, onControl}) {
    const callbacks = useRef({onBegin, onControl});
    callbacks.current = {onBegin, onControl};
    useEffect(() => {
        if (!authorized) return;
        const engine = new CallerTestEngine({
            begin: mode => callbacks.current.onBegin?.(mode),
            control: encoded => callbacks.current.onControl?.(encoded)
        });
        const bindings = [];
        const bind = (target, event, handler) => {
            if (!target) return;
            target.addEventListener(event, handler);
            bindings.push(() => target.removeEventListener(event, handler));
        };
        bind(engine.element("start"), "click", () => {
            void engine.start(engine.element("mode")?.value || "text",
                engine.element("input-device")?.value || "",
                engine.element("output-device")?.value || "");
        });
        bind(engine.element("stop"), "click", () => engine.stop());
        bind(engine.element("mute"), "click", () => engine.mute());
        bind(engine.element("interrupt"), "click", () => engine.interrupt());
        bind(engine.element("form"), "submit", event => {
            event.preventDefault(); engine.submit(engine.element("text")?.value || "");
        });
        bind(engine.element("device-refresh"), "click", () => { void engine.devices(); });
        bind(engine.element("output-pick"), "click", () => { void engine.chooseOutput(); });
        bind(engine.element("mode"), "change", () => {
            if (!engine.terminal) return;
            const speech = engine.element("mode").value === "speech";
            if (engine.element("devices")) engine.element("devices").hidden = !speech;
            if (engine.element("form")) engine.element("form").hidden = speech;
            engine.purge();
        });
        bind(document, "visibilitychange", () => {
            if (document.visibilityState !== "visible") engine.stop();
        });
        bind(window, "pagehide", () => engine.stop());
        bind(window, "beforeunload", () => engine.stop());
        bind(navigator.mediaDevices, "devicechange", () => {
            if (!engine.terminal && engine.mode === "speech") engine.stop("unavailable");
            engine.write("device-status", "Device changed");
        });
        const output = engine.element("output-device");
        if (output && typeof window.AudioContext?.prototype?.setSinkId !== "function")
            output.disabled = true;
        const picker = engine.element("output-pick");
        if (picker && typeof navigator.mediaDevices?.selectAudioOutput !== "function")
            picker.disabled = true;
        return () => { engine.dispose(); for (const dispose of bindings) dispose(); };
    }, [authorized]);
    return null;
}
"""
