import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { expect, it } from 'vitest';

it.each(['en', 'de', 'fr', 'it', 'ja', 'ko', 'zh-CN', 'zh-TW'])('R1-7: %s approximate location notice includes the reason', (locale) => {
  const catalog = JSON.parse(readFileSync(`messages/${locale}/settings.json`, 'utf8'));
  expect(catalog.location.approximateDetectedWithReason).toContain('{reason}');
});

it('R1-9: changed event handlers use descriptive parameter names', () => {
  const source = ts.createSourceFile('settings.tsx', readFileSync('app/dashboard/settings/page.tsx', 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const parameters: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isJsxAttribute(node) && node.name.getText(source) === 'onChange' && node.initializer && ts.isJsxExpression(node.initializer) && node.initializer.expression && ts.isArrowFunction(node.initializer.expression)) {
      parameters.push(...node.initializer.expression.parameters.map((parameter) => parameter.name.getText(source)));
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  expect(parameters).not.toContain('e');
});
