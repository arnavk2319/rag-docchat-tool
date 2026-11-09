const msg = (what) => `${what} is banned (ADR-0004): render text nodes only.`

export const noDangerousHtml = [
  'error',
  {
    selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
    message: msg('dangerouslySetInnerHTML'),
  },
  {
    selector:
      "Property[key.name='dangerouslySetInnerHTML'], Property[key.value='dangerouslySetInnerHTML']",
    message: msg('dangerouslySetInnerHTML'),
  },
  {
    selector:
      'AssignmentExpression > MemberExpression[property.name=/^(innerHTML|outerHTML)$/], ' +
      'AssignmentExpression > MemberExpression[property.value=/^(innerHTML|outerHTML)$/]',
    message: msg('innerHTML/outerHTML assignment'),
  },
  {
    selector: "CallExpression[callee.property.name='insertAdjacentHTML']",
    message: msg('insertAdjacentHTML'),
  },
  {
    selector:
      "CallExpression[callee.object.name='document'][callee.property.name=/^(write|writeln)$/]",
    message: msg('document.write'),
  },
]
