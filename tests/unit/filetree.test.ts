import { describe, expect, it } from "vitest";
import { getFileTreeIconVariant } from "../../src/renderer/components/FileTree/FileTree";

describe("getFileTreeIconVariant", () => {
  it("uses one folder variant for directories regardless of open state", () => {
    expect(
      getFileTreeIconVariant({ isDirectory: true, expanded: false, loading: false })
    ).toBe("folder");
    expect(
      getFileTreeIconVariant({ isDirectory: true, expanded: true, loading: false })
    ).toBe("folder");
    expect(
      getFileTreeIconVariant({ isDirectory: true, expanded: false, loading: true })
    ).toBe("folder");
  });

  it("uses a file variant for files", () => {
    expect(
      getFileTreeIconVariant({ isDirectory: false, expanded: false, loading: false })
    ).toBe("file");
  });
});
