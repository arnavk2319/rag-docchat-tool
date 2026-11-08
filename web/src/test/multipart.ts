/** Builds a raw multipart body (jsdom's FormData loses File names when serialised by Node fetch). */
export function multipart(files: { name: string; content: string; type?: string }[]) {
  const boundary = '----testboundary'
  const body =
    files
      .map(
        (f) =>
          `--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="${f.name}"\r\n` +
          `Content-Type: ${f.type ?? 'text/plain'}\r\n\r\n${f.content}\r\n`,
      )
      .join('') + `--${boundary}--\r\n`
  return { body, headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` } }
}
