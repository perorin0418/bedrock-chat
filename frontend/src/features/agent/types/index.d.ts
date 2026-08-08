import {
  AgentToolResultContent,
  RelatedDocument,
} from '../../../@types/conversation';

export type AgentInput = {
  tools: AgentTool[];
};

export type FirecrawlConfig = {
  apiKey: string;
  maxResults: number;
};

export type SearchEngine = 'duckduckgo' | 'firecrawl';
export type ToolType = 'internet' | 'plain' | 'bedrock_agent' | 'mcp';

export type BedrockAgentConfig = {
  agentId: string;
  aliasId: string;
};

export type McpAuthType =
  | 'cognito_client_credentials'
  | 'none'
  | 'bearer_token'
  | 'basic_auth';

export type McpConfig = {
  label: string;
  endpointUrl: string;
  authType: McpAuthType;
  clientId?: string;
  clientSecret?: string;
  bearerToken?: string;
  username?: string;
  basicAuthToken?: string;
};

export type InternetAgentTool = {
  toolType: 'internet';
  name: string;
  description: string;
  searchEngine: SearchEngine;
  firecrawlConfig?: FirecrawlConfig;
};

export type PlainAgentTool = {
  toolType: 'plain';
  name: string;
  description: string;
};

export type BedrockAgentTool = {
  toolType: 'bedrock_agent';
  name: string;
  description: string;
  bedrockAgentConfig?: BedrockAgentConfig;
};

export type McpAgentTool = {
  toolType: 'mcp';
  name: string;
  description: string;
  mcpServers: McpConfig[];
};

export type AgentTool =
  | InternetAgentTool
  | PlainAgentTool
  | BedrockAgentTool
  | McpAgentTool;

export type Agent = {
  tools: AgentTool[];
};

export type AgentToolsProps = {
  /** ReasoningContent in thinkingLog of assistant message. */
  reasoning?: string;
  /** TextContent in thinkingLog of assistant message. */
  thought?: string;
  tools: {
    // Note: key is toolUseId
    [key: string]: AgentToolUse;
  };
};

export type AgentToolUse = {
  name: string;
  status: AgentToolState;
  input: { [key: string]: any }; // eslint-disable-line @typescript-eslint/no-explicit-any
  resultContents?: AgentToolResultContent[];
  relatedDocuments?: RelatedDocument[];
};

export type AgentToolState = 'running' | 'success' | 'error';
