import { afterEach, describe, expect, it } from "vitest";
import {
  clearBufferedTerminalOutput,
  consumeBufferedTerminalOutput,
  createBufferedTerminalTarget,
  flushBufferedTerminalTarget,
  routeTerminalOutput,
} from "../../src/renderer/components/Terminal/terminalOutputBuffer";

describe("terminal output buffering", () => {
  afterEach(() => {
    clearBufferedTerminalOutput();
  });

  it("buffers output that arrives before a terminal cache exists", () => {
    routeTerminalOutput({
      terminalId: "term-42",
      data: "boot line\n",
    });

    expect(consumeBufferedTerminalOutput("term-42")).toBe("boot line\n");
  });

  it("hydrates a new terminal target with early buffered output once the pane is ready", () => {
    routeTerminalOutput({
      terminalId: "term-42",
      data: "boot line\n",
    });

    const writes: string[] = [];
    const target = createBufferedTerminalTarget((chunk) => {
      writes.push(chunk);
    });

    routeTerminalOutput({
      terminalId: "term-42",
      data: "prompt> ",
      target,
      windowResizing: false,
    });

    target.opened = true;
    flushBufferedTerminalTarget(target);

    expect(writes.join("")).toBe("boot line\nprompt> ");
    expect(target.pendingOutput).toBe("");
  });
});
