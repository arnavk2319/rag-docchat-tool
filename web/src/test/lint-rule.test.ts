import { ESLint } from 'eslint'
import { resolve } from 'node:path'
import tseslint from 'typescript-eslint'
import { noDangerousHtml } from '../../eslint-rules.js'

const eslint = new ESLint({
  overrideConfigFile: true,
  overrideConfig: [
    {
      files: ['**/*.{ts,tsx}'],
      languageOptions: {
        parser: tseslint.parser,
        parserOptions: { ecmaFeatures: { jsx: true } },
      },
      rules: { 'no-restricted-syntax': noDangerousHtml },
    },
  ],
})

const lint = async (code: string) =>
  (await eslint.lintText(code, { filePath: 'x.tsx' }))[0].messages

describe('no-restricted-syntax HTML sinks', () => {
  it('rejects dangerouslySetInnerHTML (fixture file)', async () => {
    const file = resolve(process.cwd(), 'src/test/fixtures/dangerous.tsx')
    const [res] = await eslint.lintFiles([file])
    expect(res.messages).toHaveLength(1)
    expect(res.messages[0].message).toContain('dangerouslySetInnerHTML is banned')
  })

  const bad: [string, string, string][] = [
    ['innerHTML assignment', 'el.innerHTML = x', 'innerHTML/outerHTML assignment'],
    ['bracket innerHTML', "el['innerHTML'] = x", 'innerHTML/outerHTML assignment'],
    ['outerHTML assignment', 'el.outerHTML = x', 'innerHTML/outerHTML assignment'],
    [
      'createElement props',
      "React.createElement('div', {dangerouslySetInnerHTML: {__html: x}})",
      'dangerouslySetInnerHTML',
    ],
    ['quoted prop key', "const p = {'dangerouslySetInnerHTML': 1}", 'dangerouslySetInnerHTML'],
    ['insertAdjacentHTML', "el.insertAdjacentHTML('beforeend', x)", 'insertAdjacentHTML'],
    ['document.write', 'document.write(x)', 'document.write'],
  ]
  it.each(bad)('rejects %s', async (_n, code, text) => {
    const msgs = await lint(code)
    expect(msgs).toHaveLength(1)
    expect(msgs[0].ruleId).toBe('no-restricted-syntax')
    expect(msgs[0].message).toContain(text)
  })

  it('accepts safe code', async () => {
    const msgs = await lint(
      "const a = el.textContent; el.textContent = 'x'; const b = <div>{a}</div>; const o = {html: 1}",
    )
    expect(msgs).toEqual([])
  })
})
