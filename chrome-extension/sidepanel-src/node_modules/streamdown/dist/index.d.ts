import * as react_jsx_runtime from 'react/jsx-runtime';
import * as react from 'react';
import react__default, { SVGProps, JSX, ComponentType, HTMLAttributes, ComponentProps } from 'react';
import { MermaidConfig } from 'mermaid';
import { RemendOptions } from 'remend';
import { Pluggable, PluggableList } from 'unified';
import { Element, Parents } from 'hast';
import { Options as Options$1 } from 'remark-rehype';
import { BundledTheme, ThemeRegistrationAny, BundledLanguage } from 'shiki';
export { BundledLanguage, BundledTheme, ThemeRegistrationAny } from 'shiki';

interface AnimatePlugin {
    /**
     * Returns the total HAST text node character count from the last
     * rehype run, then resets to 0. Use this value as the argument to
     * setPrevContentLength on the next render.
     */
    getLastRenderCharCount: () => number;
    name: "animate";
    rehypePlugin: Pluggable;
    /**
     * Set the number of HAST text characters from a previous render.
     * Characters up to this count will get duration=0ms, preventing
     * re-animation of already-visible content during streaming updates.
     */
    setPrevContentLength: (length: number) => void;
    type: "animate";
}
interface AnimateOptions {
    animation?: "fadeIn" | "blurIn" | "slideUp" | (string & {});
    duration?: number;
    easing?: string;
    sep?: "word" | "char";
    stagger?: number;
}
declare function createAnimatePlugin(options?: AnimateOptions): AnimatePlugin;

type IconComponent = React.ComponentType<SVGProps<SVGSVGElement> & {
    size?: number;
}>;
interface IconMap {
    CheckIcon: IconComponent;
    CopyIcon: IconComponent;
    DownloadIcon: IconComponent;
    ExternalLinkIcon: IconComponent;
    Loader2Icon: IconComponent;
    Maximize2Icon: IconComponent;
    RotateCcwIcon: IconComponent;
    XIcon: IconComponent;
    ZoomInIcon: IconComponent;
    ZoomOutIcon: IconComponent;
}

interface ExtraProps {
    node?: Element | undefined;
}
type AllowElement = (element: Readonly<Element>, index: number, parent: Readonly<Parents> | undefined) => boolean | null | undefined;
type UrlTransform = (url: string, key: string, node: Readonly<Element>) => string | null | undefined;
type Components = {
    [Key in keyof JSX.IntrinsicElements]?: ComponentType<JSX.IntrinsicElements[Key] & ExtraProps> | keyof JSX.IntrinsicElements;
} & {
    inlineCode?: ComponentType<JSX.IntrinsicElements["code"] & ExtraProps>;
    [key: string]: ComponentType<Record<string, unknown> & ExtraProps> | keyof JSX.IntrinsicElements | undefined;
};
interface Options {
    allowElement?: AllowElement;
    allowedElements?: readonly string[];
    children?: string;
    components?: Components;
    disallowedElements?: readonly string[];
    rehypePlugins?: PluggableList;
    remarkPlugins?: PluggableList;
    remarkRehypeOptions?: Readonly<Options$1>;
    skipHtml?: boolean;
    unwrapDisallowed?: boolean;
    urlTransform?: UrlTransform;
}
declare const defaultUrlTransform: UrlTransform;

type ThemeInput = BundledTheme | ThemeRegistrationAny;
/**
 * A single token in a highlighted line
 */
interface HighlightToken {
    bgColor?: string;
    color?: string;
    content: string;
    htmlAttrs?: Record<string, string>;
    htmlStyle?: Record<string, string>;
    offset?: number;
}
/**
 * Result from code highlighting (compatible with shiki's TokensResult)
 */
interface HighlightResult {
    bg?: string;
    fg?: string;
    rootStyle?: string | false;
    tokens: HighlightToken[][];
}
/**
 * Options for highlighting code
 */
interface HighlightOptions {
    code: string;
    language: BundledLanguage;
    themes: [ThemeInput, ThemeInput];
}
/**
 * Plugin for code syntax highlighting (Shiki)
 */
interface CodeHighlighterPlugin {
    /**
     * Get list of supported languages
     */
    getSupportedLanguages: () => BundledLanguage[];
    /**
     * Get the configured themes
     */
    getThemes: () => [ThemeInput, ThemeInput];
    /**
     * Highlight code and return tokens
     * Returns null if highlighting not ready yet (async loading)
     * Use callback for async result
     */
    highlight: (options: HighlightOptions, callback?: (result: HighlightResult) => void) => HighlightResult | null;
    name: "shiki";
    /**
     * Check if language is supported
     */
    supportsLanguage: (language: BundledLanguage) => boolean;
    type: "code-highlighter";
}
/**
 * Mermaid instance interface
 */
interface MermaidInstance {
    initialize: (config: MermaidConfig) => void;
    render: (id: string, source: string) => Promise<{
        svg: string;
    }>;
}
/**
 * Plugin for diagram rendering (Mermaid)
 */
interface DiagramPlugin {
    /**
     * Get the mermaid instance (initialized with optional config)
     */
    getMermaid: (config?: MermaidConfig) => MermaidInstance;
    /**
     * Language identifier for code blocks
     */
    language: string;
    name: "mermaid";
    type: "diagram";
}
/**
 * Plugin for math rendering (KaTeX)
 */
interface MathPlugin {
    /**
     * Get CSS styles for math rendering (injected into head)
     */
    getStyles?: () => string;
    name: "katex";
    /**
     * Get rehype plugin for rendering math
     */
    rehypePlugin: Pluggable;
    /**
     * Get remark plugin for parsing math syntax
     */
    remarkPlugin: Pluggable;
    type: "math";
}
/**
 * Plugin for CJK text handling
 */
interface CjkPlugin {
    name: "cjk";
    /**
     * @deprecated Use remarkPluginsBefore and remarkPluginsAfter instead
     * All remark plugins (for backwards compatibility)
     */
    remarkPlugins: Pluggable[];
    /**
     * Remark plugins that must run AFTER remarkGfm
     * (e.g., autolink boundary splitting, strikethrough enhancements)
     */
    remarkPluginsAfter: Pluggable[];
    /**
     * Remark plugins that must run BEFORE remarkGfm
     * (e.g., remark-cjk-friendly which modifies emphasis handling)
     */
    remarkPluginsBefore: Pluggable[];
    type: "cjk";
}
interface CustomRendererProps {
    code: string;
    isIncomplete: boolean;
    language: string;
    /** Raw metastring from the code fence (everything after the language identifier).
     * e.g. ```rust {1} title="foo"  →  meta = '{1} title="foo"'
     * Undefined when no metastring is present. */
    meta?: string;
}
interface CustomRenderer {
    component: react__default.ComponentType<CustomRendererProps>;
    language: string | string[];
}
/**
 * Plugin configuration passed to Streamdown
 */
interface PluginConfig {
    cjk?: CjkPlugin;
    code?: CodeHighlighterPlugin;
    math?: MathPlugin;
    mermaid?: DiagramPlugin;
    renderers?: CustomRenderer[];
}

interface StreamdownTranslations {
    close: string;
    copied: string;
    copyCode: string;
    copyLink: string;
    copyTable: string;
    copyTableAsCsv: string;
    copyTableAsMarkdown: string;
    copyTableAsTsv: string;
    downloadDiagram: string;
    downloadDiagramAsMmd: string;
    downloadDiagramAsPng: string;
    downloadDiagramAsSvg: string;
    downloadFile: string;
    downloadImage: string;
    downloadTable: string;
    downloadTableAsCsv: string;
    downloadTableAsMarkdown: string;
    exitFullscreen: string;
    externalLinkWarning: string;
    imageNotAvailable: string;
    mermaidFormatMmd: string;
    mermaidFormatPng: string;
    mermaidFormatSvg: string;
    openExternalLink: string;
    openLink: string;
    tableFormatCsv: string;
    tableFormatMarkdown: string;
    tableFormatTsv: string;
    viewFullscreen: string;
}
declare const defaultTranslations: StreamdownTranslations;

/**
 * Hook to check if the current block has an incomplete (unclosed) code fence.
 *
 * Returns `true` when the code fence in this block is still being streamed.
 * Useful for deferring expensive renders (syntax highlighting, Mermaid diagrams)
 * until the code block is complete.
 */
declare const useIsCodeFenceIncomplete: () => boolean;

type CodeBlockProps = HTMLAttributes<HTMLDivElement> & {
    code: string;
    language: string;
    /** Whether the code block is still being streamed (incomplete) */
    isIncomplete?: boolean;
    /** Custom starting line number for line numbering (default: 1) */
    startLine?: number;
    /** Show line numbers in code blocks. @default true */
    lineNumbers?: boolean;
};
declare const CodeBlock: ({ code, language, className, children, isIncomplete, startLine, lineNumbers, ...rest }: CodeBlockProps) => react_jsx_runtime.JSX.Element;

type CodeBlockContainerProps = ComponentProps<"div"> & {
    language: string;
    /** Whether the code block is still being streamed (incomplete) */
    isIncomplete?: boolean;
};
declare const CodeBlockContainer: ({ className, language, style, isIncomplete, ...props }: CodeBlockContainerProps) => react_jsx_runtime.JSX.Element;

type CodeBlockCopyButtonProps = ComponentProps<"button"> & {
    onCopy?: () => void;
    onError?: (error: Error) => void;
    timeout?: number;
};
declare const CodeBlockCopyButton: ({ onCopy, onError, timeout, children, className, code: propCode, ...props }: CodeBlockCopyButtonProps & {
    code?: string;
}) => react_jsx_runtime.JSX.Element;

type CodeBlockDownloadButtonProps = ComponentProps<"button"> & {
    onDownload?: () => void;
    onError?: (error: Error) => void;
};
declare const CodeBlockDownloadButton: ({ onDownload, onError, language, children, className, code: propCode, ...props }: CodeBlockDownloadButtonProps & {
    code?: string;
    language?: string;
}) => react_jsx_runtime.JSX.Element;

interface CodeBlockHeaderProps {
    language: string;
}
declare const CodeBlockHeader: ({ language }: CodeBlockHeaderProps) => react_jsx_runtime.JSX.Element;

declare const CodeBlockSkeleton: () => react_jsx_runtime.JSX.Element;

/**
 * Detect text direction using the "first strong character" algorithm.
 * Strips common markdown syntax then finds the first Unicode letter
 * with strong directionality.
 *
 * Note: markdown stripping is best-effort — nested formatting,
 * multi-line fenced code blocks, and raw HTML are not fully handled.
 * This is acceptable since the algorithm only needs to reach the first
 * strong character, which is almost always in plain prose.
 *
 * @returns "rtl" if first strong char is RTL, "ltr" otherwise
 */
declare function detectTextDirection(text: string): "ltr" | "rtl";

declare const parseMarkdownIntoBlocks: (markdown: string) => string[];

interface TableCopyDropdownProps {
    children?: React.ReactNode;
    className?: string;
    onCopy?: (format: "csv" | "tsv" | "md") => void;
    onError?: (error: Error) => void;
    timeout?: number;
}
declare const TableCopyDropdown: ({ children, className, onCopy, onError, timeout, }: TableCopyDropdownProps) => react_jsx_runtime.JSX.Element;

interface TableDownloadButtonProps {
    children?: React.ReactNode;
    className?: string;
    filename?: string;
    format?: "csv" | "markdown";
    onDownload?: () => void;
    onError?: (error: Error) => void;
}
declare const TableDownloadButton: ({ children, className, onDownload, onError, format, filename, }: TableDownloadButtonProps) => react_jsx_runtime.JSX.Element;
interface TableDownloadDropdownProps {
    children?: React.ReactNode;
    className?: string;
    onDownload?: (format: "csv" | "markdown") => void;
    onError?: (error: Error) => void;
}
declare const TableDownloadDropdown: ({ children, className, onDownload, onError, }: TableDownloadDropdownProps) => react_jsx_runtime.JSX.Element;

interface TableData {
    headers: string[];
    rows: string[][];
}
declare const extractTableDataFromElement: (tableElement: HTMLElement) => TableData;
declare const tableDataToCSV: (data: TableData) => string;
declare const tableDataToTSV: (data: TableData) => string;
declare const escapeMarkdownTableCell: (cell: string) => string;
declare const tableDataToMarkdown: (data: TableData) => string;

/**
 * Normalizes indentation in HTML blocks to prevent Markdown parsers from
 * treating indented HTML tags as code blocks (4+ spaces = code in Markdown).
 *
 * Useful when rendering AI-generated HTML content with nested tags that
 * are indented for readability.
 *
 * @param content - The raw HTML/Markdown string to normalize
 * @returns The normalized string with reduced indentation before HTML tags
 */
declare const normalizeHtmlIndentation: (content: string) => string;
type ControlsConfig = boolean | {
    table?: boolean | {
        copy?: boolean;
        download?: boolean;
        fullscreen?: boolean;
    };
    code?: boolean | {
        copy?: boolean;
        download?: boolean;
    };
    mermaid?: boolean | {
        download?: boolean;
        copy?: boolean;
        fullscreen?: boolean;
        panZoom?: boolean;
    };
};
interface LinkSafetyModalProps {
    isOpen: boolean;
    onClose: () => void;
    onConfirm: () => void;
    url: string;
}
interface LinkSafetyConfig {
    enabled: boolean;
    onLinkCheck?: (url: string) => Promise<boolean> | boolean;
    renderModal?: (props: LinkSafetyModalProps) => React.ReactNode;
}
interface MermaidErrorComponentProps {
    chart: string;
    error: string;
    retry: () => void;
}
interface MermaidOptions {
    config?: MermaidConfig;
    errorComponent?: React.ComponentType<MermaidErrorComponentProps>;
}
type AllowedTags = Record<string, string[]>;
type StreamdownProps = Options & {
    mode?: "static" | "streaming";
    /** Text direction for blocks. "auto" detects per-block using first strong character algorithm. */
    dir?: "auto" | "ltr" | "rtl";
    BlockComponent?: React.ComponentType<BlockProps>;
    parseMarkdownIntoBlocksFn?: (markdown: string) => string[];
    parseIncompleteMarkdown?: boolean;
    /** Normalize HTML block indentation to prevent 4+ spaces being treated as code blocks. @default false */
    normalizeHtmlIndentation?: boolean;
    className?: string;
    shikiTheme?: [ThemeInput, ThemeInput];
    mermaid?: MermaidOptions;
    controls?: ControlsConfig;
    isAnimating?: boolean;
    animated?: boolean | AnimateOptions;
    caret?: keyof typeof carets;
    plugins?: PluginConfig;
    remend?: RemendOptions;
    linkSafety?: LinkSafetyConfig;
    /** Custom tags to allow through sanitization with their permitted attributes */
    allowedTags?: AllowedTags;
    /**
     * Tags whose children should be treated as plain text (no markdown parsing).
     * Useful for mention/entity tags in AI UIs where child content is a data
     * label rather than prose. Requires the tag to also be listed in `allowedTags`.
     *
     * @example
     * ```tsx
     * <Streamdown
     *   allowedTags={{ mention: ['user_id'] }}
     *   literalTagContent={['mention']}
     * >
     *   {`<mention user_id="123">@_some_username_</mention>`}
     * </Streamdown>
     * ```
     */
    literalTagContent?: string[];
    /** Override UI strings for i18n / custom labels */
    translations?: Partial<StreamdownTranslations>;
    /** Custom icons to override the default icons used in controls */
    icons?: Partial<IconMap>;
    /** Tailwind CSS prefix to prepend to all utility classes (e.g. `"tw"` produces `tw:flex` instead of `flex`). Enables Tailwind v4's `prefix()` support. Note: user-supplied `className` values are also prefixed. */
    prefix?: string;
    /** Show line numbers in code blocks. @default true */
    lineNumbers?: boolean;
    /** Called when isAnimating transitions from false to true. Suppressed in mode="static". */
    onAnimationStart?: () => void;
    /** Called when isAnimating transitions from true to false. Suppressed in mode="static". */
    onAnimationEnd?: () => void;
};
declare const defaultRehypePlugins: Record<string, Pluggable>;
declare const defaultRemarkPlugins: Record<string, Pluggable>;
declare const carets: {
    block: string;
    circle: string;
};
interface StreamdownContextType {
    controls: ControlsConfig;
    isAnimating: boolean;
    /** Show line numbers in code blocks. @default true */
    lineNumbers: boolean;
    linkSafety?: LinkSafetyConfig;
    mermaid?: MermaidOptions;
    mode: "static" | "streaming";
    shikiTheme: [ThemeInput, ThemeInput];
}
declare const StreamdownContext: react.Context<StreamdownContextType>;
type BlockProps = Options & {
    content: string;
    shouldParseIncompleteMarkdown: boolean;
    shouldNormalizeHtmlIndentation: boolean;
    index: number;
    /** Whether this block is incomplete (still being streamed) */
    isIncomplete: boolean;
    /** Resolved text direction for this block */
    dir?: "ltr" | "rtl";
    /** Animate plugin instance for tracking previous content length */
    animatePlugin?: AnimatePlugin | null;
};
declare const Block: react.MemoExoticComponent<({ content, shouldParseIncompleteMarkdown: _, shouldNormalizeHtmlIndentation, index: __, isIncomplete, dir, animatePlugin: animatePluginProp, ...props }: BlockProps) => react_jsx_runtime.JSX.Element>;
declare const Streamdown: react.MemoExoticComponent<({ children, mode, dir, parseIncompleteMarkdown: shouldParseIncompleteMarkdown, normalizeHtmlIndentation: shouldNormalizeHtmlIndentation, components, rehypePlugins, remarkPlugins, className, shikiTheme, mermaid, controls, isAnimating, animated, BlockComponent, parseMarkdownIntoBlocksFn, caret, plugins, remend: remendOptions, linkSafety, lineNumbers, allowedTags, literalTagContent, translations, icons: iconOverrides, prefix, onAnimationStart, onAnimationEnd, ...props }: StreamdownProps) => react_jsx_runtime.JSX.Element>;

export { type AllowElement, type AllowedTags, type AnimateOptions, Block, type BlockProps, type CjkPlugin, CodeBlock, CodeBlockContainer, CodeBlockCopyButton, CodeBlockDownloadButton, CodeBlockHeader, CodeBlockSkeleton, type CodeHighlighterPlugin, type Components, type ControlsConfig, type CustomRenderer, type CustomRendererProps, type DiagramPlugin, type ExtraProps, type HighlightOptions, type IconMap, type LinkSafetyConfig, type LinkSafetyModalProps, type MathPlugin, type MermaidErrorComponentProps, type MermaidOptions, type PluginConfig, Streamdown, StreamdownContext, type StreamdownContextType, type StreamdownProps, type StreamdownTranslations, TableCopyDropdown, type TableCopyDropdownProps, type TableData, TableDownloadButton, type TableDownloadButtonProps, TableDownloadDropdown, type TableDownloadDropdownProps, type ThemeInput, type UrlTransform, createAnimatePlugin, defaultRehypePlugins, defaultRemarkPlugins, defaultTranslations, defaultUrlTransform, detectTextDirection, escapeMarkdownTableCell, extractTableDataFromElement, normalizeHtmlIndentation, parseMarkdownIntoBlocks, tableDataToCSV, tableDataToMarkdown, tableDataToTSV, useIsCodeFenceIncomplete };
