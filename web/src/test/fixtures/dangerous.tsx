// Fixture for the lint-rule test: must be rejected by no-restricted-syntax.
export const Bad = ({ html }: { html: string }) => (
  <div dangerouslySetInnerHTML={{ __html: html }} />
)
