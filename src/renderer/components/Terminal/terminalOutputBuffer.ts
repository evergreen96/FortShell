export type BufferedTerminalTarget = {
  opened: boolean;
  pendingOutput: string;
  write: (data: string) => void;
};

const bufferedTerminalOutput = new Map<string, string>();

export function clearBufferedTerminalOutput(): void {
  bufferedTerminalOutput.clear();
}

export function consumeBufferedTerminalOutput(terminalId: string): string {
  const output = bufferedTerminalOutput.get(terminalId) ?? "";
  bufferedTerminalOutput.delete(terminalId);
  return output;
}

export function createBufferedTerminalTarget(
  write: (data: string) => void,
): BufferedTerminalTarget {
  return {
    opened: false,
    pendingOutput: "",
    write,
  };
}

export function flushBufferedTerminalTarget(
  target: BufferedTerminalTarget,
): void {
  if (!target.pendingOutput) {
    return;
  }

  const output = target.pendingOutput;
  target.pendingOutput = "";
  target.write(output);
}

export function routeTerminalOutput(input: {
  terminalId: string;
  data: string;
  target?: BufferedTerminalTarget;
  windowResizing?: boolean;
}): void {
  const target = input.target;

  if (!target) {
    bufferedTerminalOutput.set(
      input.terminalId,
      `${bufferedTerminalOutput.get(input.terminalId) ?? ""}${input.data}`,
    );
    return;
  }

  const bufferedOutput = consumeBufferedTerminalOutput(input.terminalId);
  if (bufferedOutput) {
    target.pendingOutput += bufferedOutput;
  }

  if (!target.opened || input.windowResizing) {
    target.pendingOutput += input.data;
    return;
  }

  flushBufferedTerminalTarget(target);
  target.write(input.data);
}
