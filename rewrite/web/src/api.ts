export type FileKind = "file" | "folder";

export type FileNode = {
  id: string | number;
  name: string;
  type: FileKind;
  size: number;
  selected: boolean;
  progress?: number;
  children?: FileNode[];
};

export type SelectionStats = {
  selectedCount: number;
  totalCount: number;
  selectedSize: number;
  totalSize: number;
};

export type TorrentTreeResponse = {
  error?: string;
  message?: string;
  engine?: string;
  files?: FileNode[];
};

export type RenameRequest = {
  old_path: string;
  new_path: string;
  type: FileKind;
};

export function flattenFiles(nodes: FileNode[]): FileNode[] {
  const out: FileNode[] = [];
  const stack = [...nodes].reverse();
  while (stack.length > 0) {
    const node = stack.pop()!;
    out.push(node);
    if (node.children) {
      for (let i = node.children.length - 1; i >= 0; i -= 1) {
        stack.push(node.children[i]);
      }
    }
  }
  return out;
}

export function calculateFolderSize(folder: FileNode): number {
  let total = 0;
  const stack: FileNode[] = [folder];
  while (stack.length > 0) {
    const node = stack.pop()!;
    if (node.type === "file") {
      total += Math.max(0, node.size || 0);
    } else if (node.children) {
      stack.push(...node.children);
    }
  }
  return total;
}

export function calculateStats(nodes: FileNode[]): SelectionStats {
  const stats: SelectionStats = {
    selectedCount: 0,
    totalCount: 0,
    selectedSize: 0,
    totalSize: 0,
  };
  const stack = [...nodes];
  while (stack.length > 0) {
    const node = stack.pop()!;
    if (node.type === "file") {
      const size = Math.max(0, node.size || 0);
      stats.totalCount += 1;
      stats.totalSize += size;
      if (node.selected) {
        stats.selectedCount += 1;
        stats.selectedSize += size;
      }
    }
    if (node.children) stack.push(...node.children);
  }
  return stats;
}

export function areAllChildrenSelected(folder: FileNode): boolean {
  const children = folder.children ?? [];
  return children.length > 0 && children.every((child) =>
    child.type === "folder" ? areAllChildrenSelected(child) : child.selected,
  );
}

export function areSomeChildrenSelected(folder: FileNode): boolean {
  return (folder.children ?? []).some((child) =>
    child.type === "folder" ? areSomeChildrenSelected(child) : child.selected,
  );
}

export function toggleFolder(folder: FileNode, selected: boolean): void {
  const stack: FileNode[] = [folder];
  while (stack.length > 0) {
    const node = stack.pop()!;
    node.selected = selected;
    if (node.children) stack.push(...node.children);
  }
}

export function invertFolderSelection(folder: FileNode): void {
  const stack: FileNode[] = [folder];
  while (stack.length > 0) {
    const node = stack.pop()!;
    node.selected = !node.selected;
    if (node.children) stack.push(...node.children);
  }
}

export function formatSize(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Math.max(0, Number.isFinite(bytes) ? bytes : 0);
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(2)} ${units[index]}`;
}

function torrentUrl(gid: string, pin: string, mode: string): string {
  const query = new URLSearchParams({ gid, pin, mode });
  return `/app/files/torrent?${query.toString()}`;
}

export async function getTorrentTree(gid: string, pin: string): Promise<TorrentTreeResponse> {
  const response = await fetch(torrentUrl(gid, pin, "get"));
  if (!response.ok) throw new Error(`torrent get failed: ${response.status}`);
  return response.json() as Promise<TorrentTreeResponse>;
}

export async function submitSelection(gid: string, pin: string, files: FileNode[]): Promise<void> {
  const response = await fetch(torrentUrl(gid, pin, "selection"), {
    method: "POST",
    body: JSON.stringify(files),
  });
  if (!response.ok) throw new Error(`selection failed: ${response.status}`);
}

export async function renameTorrentEntry(
  gid: string,
  pin: string,
  request: RenameRequest,
): Promise<void> {
  const response = await fetch(torrentUrl(gid, pin, "rename"), {
    method: "POST",
    body: JSON.stringify(request),
  });
  if (!response.ok) throw new Error(`rename failed: ${response.status}`);
}
