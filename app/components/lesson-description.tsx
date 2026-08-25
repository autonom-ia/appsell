import { Fragment } from "react";

import { parseLessonMarkup, type BlockNode, type InlineNode } from "@/lib/lesson-markup";

/**
 * Renders a lesson description as React elements.
 *
 * There is deliberately no `dangerouslySetInnerHTML` anywhere in this file, and
 * no string of markup is ever built. Each node becomes an element whose only
 * child is text, so a description cannot reach the browser's HTML parser at all
 * — the safety is structural rather than a sanitiser that has to be trusted to
 * be exhaustive.
 */
export function LessonDescription({ content, emptyLabel }: {
  content: string;
  emptyLabel: string;
}) {
  const blocks = parseLessonMarkup(content);
  if (blocks.length === 0) {
    return <p className="lesson-content-empty">{emptyLabel}</p>;
  }
  return <>
    {blocks.map((block, index) => <LessonBlock key={index} block={block}/>)}
  </>;
}

function LessonBlock({ block }: { block: BlockNode }) {
  if (block.type === "list") {
    return <ul className="lesson-content-list">
      {block.items.map((item, index) => <li key={index}><InlineRun nodes={item}/></li>)}
    </ul>;
  }
  return <p><InlineRun nodes={block.children}/></p>;
}

function InlineRun({ nodes }: { nodes: readonly InlineNode[] }) {
  return <>
    {nodes.map((node, index) => <Fragment key={index}>{renderInline(node)}</Fragment>)}
  </>;
}

function renderInline(node: InlineNode) {
  switch (node.type) {
    case "strong":
      return <strong>{node.value}</strong>;
    case "emphasis":
      return <em>{node.value}</em>;
    case "code":
      return <code>{node.value}</code>;
    case "link":
      // The href already passed the https-only check in the parser. rel keeps
      // the opened tab from reaching back into this one.
      return <a href={node.href} target="_blank" rel="noopener noreferrer">{node.label}</a>;
    default:
      return node.value;
  }
}
