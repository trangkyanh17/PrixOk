export type FileNode = { id?: string | number; name?: string; children?: FileNode[] };

export function flattenFiles(nodes: FileNode[]): FileNode[] {
  const out: FileNode[] = [];
  const visit = (items: FileNode[]) => {
    for (const item of items) {
      out.push(item);
      if (item.children) visit(item.children);
    }
  };
  visit(nodes);
  return out;
}
