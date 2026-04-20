declare module "openclaw/plugin-sdk/plugin-entry" {
  export function definePluginEntry<T>(entry: T): T;
}

declare module "openclaw/plugin-sdk/memory-core-host-runtime-core" {
  export type AnyAgentTool = {
    label: string;
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    execute: (toolCallId: string, params: Record<string, unknown>) => unknown | Promise<unknown>;
  };

  export type OpenClawConfig = Record<string, unknown>;

  export function jsonResult(value: unknown): unknown;
  export function readNumberParam(
    params: Record<string, unknown>,
    key: string,
    options?: { required?: boolean; label?: string; integer?: boolean; strict?: boolean },
  ): number | undefined;
  export function readStringParam(
    params: Record<string, unknown>,
    key: string,
    options?: { required?: boolean; trim?: boolean; label?: string; allowEmpty?: boolean },
  ): string | undefined;
  export function resolveMemorySearchConfig(cfg: OpenClawConfig, agentId: string): unknown;
  export function resolveSessionAgentId(params: {
    sessionKey?: string;
    config: OpenClawConfig;
  }): string;
  export function listMemoryCorpusSupplements(): Array<{
    pluginId: string;
    supplement: {
      search(params: {
        query: string;
        maxResults?: number;
        agentSessionKey?: string;
      }): Promise<Array<Record<string, unknown>>>;
      get(params: {
        lookup: string;
        fromLine?: number;
        lineCount?: number;
        agentSessionKey?: string;
      }): Promise<Record<string, unknown> | null>;
    };
  }>;
}
