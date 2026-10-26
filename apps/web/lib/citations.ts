export interface DocCitation { docId: string; page: number | null }

/** Parses omnific://doc/<id>#page=<n>; returns null for any other URL. */
export function parseDocCitation(url: string): DocCitation | null {
  const m = /^omnific:\/\/doc\/([^#?\s]+)(?:#page=(\d+))?$/.exec(url);
  if (!m || !m[1]) return null;
  return { docId: decodeURIComponent(m[1]), page: m[2] ? Number(m[2]) : null };
}

/** Parses omnific://input/<filename> (a job attachment: sketch, photo or voice note); null otherwise. */
export function parseInputCitation(url: string): string | null {
  const m = /^omnific:\/\/input\/([^#?\s/]+)$/.exec(url);
  return m && m[1] ? decodeURIComponent(m[1]) : null;
}

export function docCitationLabel(title: string, page: number | null): string {
  return page === null ? title : `${title}, p. ${page}`;
}
