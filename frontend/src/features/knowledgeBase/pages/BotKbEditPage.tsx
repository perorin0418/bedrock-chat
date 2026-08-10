import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import InputText from '../../../components/InputText';
import Button from '../../../components/Button';
import useBot from '../../../hooks/useBot';
import { useNavigate, useParams } from 'react-router-dom';
import { PiCaretLeft, PiNote, PiPlus, PiTrash } from 'react-icons/pi';
import Textarea from '../../../components/Textarea';
import DialogInstructionsSamples from '../../../components/DialogInstructionsSamples';
import { produce } from 'immer';
import Alert from '../../../components/Alert';
import GenerationConfig from '../../../components/GenerationConfig';
import Select from '../../../components/Select';
import {
  BotFile,
  ConversationQuickStarter,
  ActiveModels,
} from '../../../@types/bot';
import { BedrockKnowledgeBaseType, ParsingModel } from '../types';
import { ulid } from 'ulid';
import {
  EDGE_GENERATION_PARAMS,
  DEFAULT_GENERATION_CONFIG,
} from '../../../constants';
import { Slider } from '../../../components/Slider';
import ExpandableDrawerGroup from '../../../components/ExpandableDrawerGroup';
import useErrorMessage from '../../../hooks/useErrorMessage';
import Toggle from '../../../components/Toggle';
import { useAgent } from '../../../features/agent/hooks/useAgent';
import { AgentTool, McpAuthType, McpConfig as McpConfigType } from '../../../features/agent/types';
import {
  isInternetTool,
  isBedrockAgentTool,
  isMcpTool,
} from '../../../features/agent/utils/typeGuards';
import { AvailableTools } from '../../../features/agent/components/AvailableTools';
import {
  DEFAULT_FIXED_CHUNK_PARAMS,
  DEFAULT_HIERARCHICAL_CHUNK_PARAMS,
  DEFAULT_SEMANTIC_CHUNK_PARAMS,
  EDGE_FIXED_CHUNK_PARAMS,
  EDGE_HIERARCHICAL_CHUNK_PARAMS,
  EDGE_SEMANTIC_CHUNK_PARAMS,
  EDGE_SEARCH_PARAMS,
  OPENSEARCH_ANALYZER,
  DEFAULT_SEARCH_CONFIG,
  DEFAULT_OPENSEARCH_ANALYZER,
} from '../constants';
import {
  GUARDRAILS_FILTERS_THRESHOLD,
  GUARDRAILS_CONTEXTUAL_GROUNDING_THRESHOLD,
} from '../../../constants';
import { Model } from '../../../@types/conversation';
import { AVAILABLE_MODEL_KEYS } from '../../../constants/index';
import {
  ChunkingStrategy,
  FixedSizeParams,
  HierarchicalParams,
  SemanticParams,
  EmbeddingsModel,
  OpenSearchParams,
  SearchParams,
  WebCrawlingScope,
} from '../types';
import { toCamelCase } from '../../../utils/StringUtils';
import useGlobalConfig from '../../../hooks/useGlobalConfig';
import useSnackbar from '../../../hooks/useSnackbar';

const edgeGenerationParams = EDGE_GENERATION_PARAMS;

const defaultGenerationConfig = DEFAULT_GENERATION_CONFIG;

const BotKbEditPage: React.FC = () => {
  const { i18n, t } = useTranslation();
  const navigate = useNavigate();
  const { botId: paramsBotId } = useParams();
  const { getMyBot, registerBot, updateBot } = useBot();
  const { availableTools } = useAgent();
  const { getGlobalConfig } = useGlobalConfig();
  const { data: globalConfig } = getGlobalConfig();
  const snackbar = useSnackbar();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const mcpOauth = params.get('mcpOauth');
    if (mcpOauth === 'success') {
      snackbar.open(t('agent.tools.mcpConfig.oauth.connected'));
    } else if (mcpOauth === 'error') {
      snackbar.open(t('agent.tools.mcpConfig.oauth.connectError'));
    } else {
      return;
    }
    // The backend's unknown-state failure path redirects to
    // `/bot/new?mcpOauth=error` (no bot id known at that point), so
    // `paramsBotId` can be undefined here -- only navigate to the edit
    // screen when we actually have a bot id, otherwise just strip the query
    // string from wherever we already are.
    if (paramsBotId) {
      navigate(`/bot/edit/${paramsBotId}`, { replace: true });
    } else {
      navigate('/bot/new', { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [isLoading, setIsLoading] = useState(false);

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [instruction, setInstruction] = useState('');
  const [urls, setUrls] = useState<string[]>(['']);
  const [s3Urls, setS3Urls] = useState<string[]>(['']);
  const [files, setFiles] = useState<BotFile[]>([]);
  const [unchangedFilenames, setUnchangedFilenames] = useState<string[]>([]);
  const [displayRetrievedChunks, setDisplayRetrievedChunks] = useState(true);
  const [maxTokens, setMaxTokens] = useState<number>(
    defaultGenerationConfig.maxTokens
  );
  const [topK, setTopK] = useState<number>(defaultGenerationConfig.topK);
  const [topP, setTopP] = useState<number>(defaultGenerationConfig.topP);
  const [temperature, setTemperature] = useState<number>(
    defaultGenerationConfig.temperature
  );
  const [stopSequences, setStopSequences] = useState<string>(
    defaultGenerationConfig.stopSequences?.join(',') || ''
  );
  const [budgetTokens, setBudgetTokens] = useState<number>(
    defaultGenerationConfig.reasoningParams?.budgetTokens ??
      EDGE_GENERATION_PARAMS.budgetTokens.MIN
  );
  const [promptCachingEnabled, setPromptCachingEnabled] = useState<boolean>(false);
  const [tools, setTools] = useState<AgentTool[]>([]);
  // Snapshot of the tools as last returned by the backend -- used to tell
  // whether an in-progress edit (e.g. switching an MCP server's authType to
  // 'oauth') has actually been saved yet. A successful create/update always
  // navigates away from this page, so this never needs to be refreshed
  // mid-session.
  const [savedTools, setSavedTools] = useState<AgentTool[]>([]);
  const [conversationQuickStarters, setConversationQuickStarters] = useState<
    ConversationQuickStarter[]
  >([
    {
      title: '',
      example: '',
    },
  ]);
  const [webCrawlingScope, setWebCrawlingScope] =
    useState<WebCrawlingScope>('DEFAULT');

  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string | null>(null); // Send null when creating a new bot
  const [existKnowledgeBaseId, setExistKnowledgeBaseId] = useState<
    string | null
  >(null);
  const [knowledgeBaseType, setKnowledgeBaseType] = useState<
    'new' | 'shared' | 'existing'
  >('shared');

  const bedrockKnowledgeBaseType = useMemo<BedrockKnowledgeBaseType>(() => {
    if (existKnowledgeBaseId != null) {
      return undefined;
    }
    switch (knowledgeBaseType) {
      case 'new': {
        if (files.length === 0 && urls.length === 0 && s3Urls.length === 0) {
          return undefined;
        }
        return 'dedicated';
      }
      case 'shared': {
        if (files.length === 0) {
          return undefined;
        }
        return 'shared';
      }
      case 'existing': {
        return undefined;
      }
    }
  }, [existKnowledgeBaseId, knowledgeBaseType, files, urls, s3Urls]);

  // When loading an existing bot that already has a knowledge base id(s),
  // default the radio selection to 'existing' so the UI reflects the bot state.
  useEffect(() => {
    if (existKnowledgeBaseId && knowledgeBaseType !== 'existing') {
      setKnowledgeBaseType('existing');
    }
  }, [existKnowledgeBaseId, knowledgeBaseType]);

  const [embeddingsModel, setEmbeddingsModel] =
    useState<EmbeddingsModel>('titan_v2');

  const [hateThreshold, setHateThreshold] = useState<number>(0);
  const [insultsThreshold, setInsultsThreshold] = useState<number>(0);
  const [sexualThreshold, setSexualThreshold] = useState<number>(0);
  const [violenceThreshold, setViolenceThreshold] = useState<number>(0);
  const [misconductThreshold, setMisconductThreshold] = useState<number>(0);
  const [groundingThreshold, setGroundingThreshold] = useState<number>(0);
  const [relevanceThreshold, setRelevanceThreshold] = useState<number>(0);
  const [guardrailArn, setGuardrailArn] = useState<string>('');
  const [guardrailVersion, setGuardrailVersion] = useState<string>('');

  const isGuardrailEnabled = useMemo(() => (
    hateThreshold > 0 ||
    insultsThreshold > 0 ||
    sexualThreshold > 0 ||
    violenceThreshold > 0 ||
    misconductThreshold > 0 ||
    groundingThreshold > 0 ||
    relevanceThreshold > 0
  ), [hateThreshold, insultsThreshold, sexualThreshold, violenceThreshold, misconductThreshold, groundingThreshold, relevanceThreshold]);

  const [parsingModel, setParsingModel] = useState<ParsingModel | undefined>(
    undefined
  );
  const [webCrawlingFilters, setWebCrawlingFilters] = useState<{
    includePatterns: string[];
    excludePatterns: string[];
  }>({
    includePatterns: [''],
    excludePatterns: [''],
  });

  const [activeModels, setActiveModels] = useState<ActiveModels>(() => {
    const initialState = AVAILABLE_MODEL_KEYS.reduce(
      (acc: ActiveModels, key: Model) => {
        acc[toCamelCase(key) as keyof ActiveModels] = true;
        return acc;
      },
      {} as ActiveModels
    );
    return initialState;
  });

  const activeModelsOptions: {
    key: Model;
    label: string;
    description: string;
  }[] = (() => {
    const getGeneralModels = () => {
      let availableKeys = [...AVAILABLE_MODEL_KEYS];
      
      // Filter by global configuration if available
      if (globalConfig?.globalAvailableModels && globalConfig.globalAvailableModels.length > 0) {
        availableKeys = availableKeys.filter(key => 
          globalConfig.globalAvailableModels!.includes(key)
        );
      }
      
      return availableKeys.map((key) => ({
        key: key as Model,
        label: t(`model.${key}.label`) as string,
        description: t(`model.${key}.description`) as string,
      }));
    };

    return getGeneralModels();
  })();

  const [defaultModel, setDefaultModel] = useState<Model>(
    activeModelsOptions[0]?.key ?? AVAILABLE_MODEL_KEYS[0]
  );

  useEffect(() => {
    const isDefaultModelValid = activeModelsOptions.some(
      ({ key }) => key === defaultModel
    );
    if (!isDefaultModelValid && activeModelsOptions.length > 0) {
      setDefaultModel(activeModelsOptions[0].key);
    }
  }, [activeModelsOptions, defaultModel]);

  const [chunkingStrategy, setChunkingStrategy] =
    useState<ChunkingStrategy>('default');

  const [fixedSizeParams, setFixedSizeParams] = useState<FixedSizeParams>(
    DEFAULT_FIXED_CHUNK_PARAMS
  );

  const [hierarchicalParams, setHierarchicalParams] =
    useState<HierarchicalParams>(DEFAULT_HIERARCHICAL_CHUNK_PARAMS);

  const [semanticParams, setSemanticParams] = useState<SemanticParams>(
    DEFAULT_SEMANTIC_CHUNK_PARAMS
  );

  const [openSearchParams, setOpenSearchParams] = useState<OpenSearchParams>(
    DEFAULT_OPENSEARCH_ANALYZER[i18n.language]
      ? OPENSEARCH_ANALYZER[DEFAULT_OPENSEARCH_ANALYZER[i18n.language]]
      : OPENSEARCH_ANALYZER['none']
  );

  const [searchParams, setSearchParams] = useState<SearchParams>(
    DEFAULT_SEARCH_CONFIG
  );

  const {
    errorMessages,
    setErrorMessage: setErrorMessages,
    clearAll: clearErrorMessages,
  } = useErrorMessage();

  const isNewBot = useMemo(() => {
    return paramsBotId ? false : true;
  }, [paramsBotId]);

  const botId = useMemo(() => {
    return isNewBot ? ulid() : (paramsBotId ?? '');
  }, [isNewBot, paramsBotId]);

  useEffect(() => {
    if (!isNewBot) {
      setIsLoading(true);
      getMyBot(botId)
        .then((bot) => {
          setTools(bot.agent.tools);
          setSavedTools(bot.agent.tools);
          setTitle(bot.title);
          setDescription(bot.description);
          setInstruction(bot.instruction);
          setUrls(
            bot.knowledge.sourceUrls.length === 0
              ? ['']
              : bot.knowledge.sourceUrls
          );
          setS3Urls(
            bot.knowledge.s3Urls.length === 0 ? [''] : bot.knowledge.s3Urls
          );
          switch (bot.bedrockKnowledgeBase.type) {
            case 'dedicated':
              setKnowledgeBaseType('new');
              break;

            case 'shared':
              setKnowledgeBaseType('shared');
              break;

            default:
              break;
          }
          setFiles(
            bot.knowledge.filenames.map((filename) => ({
              filename,
              status: 'UPLOADED',
            }))
          );
          setTopK(bot.generationParams.topK);
          setTopP(bot.generationParams.topP);
          setTemperature(bot.generationParams.temperature);
          setMaxTokens(bot.generationParams.maxTokens);
          setStopSequences(bot.generationParams.stopSequences.join(','));
          setBudgetTokens(bot.generationParams.reasoningParams.budgetTokens);
          setUnchangedFilenames([...bot.knowledge.filenames]);
          setDisplayRetrievedChunks(bot.displayRetrievedChunks);
          setPromptCachingEnabled(bot.promptCachingEnabled);
          if (bot.syncStatus === 'FAILED') {
            setErrorMessages(
              isSyncChunkError(bot.syncStatusReason)
                ? 'syncChunkError'
                : 'syncError',
              bot.syncStatusReason
            );
          }
          setConversationQuickStarters(
            bot.conversationQuickStarters.length > 0
              ? bot.conversationQuickStarters
              : [
                  {
                    title: '',
                    example: '',
                  },
                ]
          );
          setKnowledgeBaseId(bot.bedrockKnowledgeBase.knowledgeBaseId);
          setExistKnowledgeBaseId(
            bot.bedrockKnowledgeBase.existKnowledgeBaseId
          );
          setEmbeddingsModel(bot.bedrockKnowledgeBase!.embeddingsModel);
          setChunkingStrategy(
            bot.bedrockKnowledgeBase!.chunkingConfiguration.chunkingStrategy
          );
          if (
            bot.bedrockKnowledgeBase!.chunkingConfiguration.chunkingStrategy ==
            'fixed_size'
          ) {
            setFixedSizeParams(
              (bot.bedrockKnowledgeBase!
                .chunkingConfiguration as FixedSizeParams) ??
                DEFAULT_FIXED_CHUNK_PARAMS
            );
          } else if (
            bot.bedrockKnowledgeBase!.chunkingConfiguration.chunkingStrategy ==
            'hierarchical'
          ) {
            setHierarchicalParams(
              (bot.bedrockKnowledgeBase!
                .chunkingConfiguration as HierarchicalParams) ??
                DEFAULT_HIERARCHICAL_CHUNK_PARAMS
            );
          } else if (
            bot.bedrockKnowledgeBase!.chunkingConfiguration.chunkingStrategy ==
            'semantic'
          ) {
            setSemanticParams(
              (bot.bedrockKnowledgeBase!
                .chunkingConfiguration as SemanticParams) ??
                DEFAULT_SEMANTIC_CHUNK_PARAMS
            );
          }

          setOpenSearchParams(bot.bedrockKnowledgeBase!.openSearch);
          setSearchParams(bot.bedrockKnowledgeBase!.searchParams);
          setGuardrailArn(bot.bedrockGuardrails.guardrailArn);
          setGuardrailVersion(
            bot.bedrockGuardrails.guardrailVersion
              ? bot.bedrockGuardrails.guardrailVersion
              : ''
          );
          setHateThreshold(
            bot.bedrockGuardrails.hateThreshold
              ? bot.bedrockGuardrails.hateThreshold
              : 0
          );
          setInsultsThreshold(
            bot.bedrockGuardrails.insultsThreshold
              ? bot.bedrockGuardrails.insultsThreshold
              : 0
          );
          setSexualThreshold(
            bot.bedrockGuardrails.sexualThreshold
              ? bot.bedrockGuardrails.sexualThreshold
              : 0
          );
          setViolenceThreshold(
            bot.bedrockGuardrails.violenceThreshold
              ? bot.bedrockGuardrails.violenceThreshold
              : 0
          );
          setMisconductThreshold(
            bot.bedrockGuardrails.misconductThreshold
              ? bot.bedrockGuardrails.misconductThreshold
              : 0
          );
          setGroundingThreshold(
            bot.bedrockGuardrails.groundingThreshold
              ? bot.bedrockGuardrails.groundingThreshold
              : 0
          );
          setRelevanceThreshold(
            bot.bedrockGuardrails.relevanceThreshold
              ? bot.bedrockGuardrails.relevanceThreshold
              : 0
          );
          setParsingModel(bot.bedrockKnowledgeBase.parsingModel);
          setWebCrawlingScope(
            bot.bedrockKnowledgeBase.webCrawlingScope ?? 'DEFAULT'
          );
          setWebCrawlingFilters({
            includePatterns: bot.bedrockKnowledgeBase.webCrawlingFilters
              ?.includePatterns || [''],
            excludePatterns: bot.bedrockKnowledgeBase.webCrawlingFilters
              ?.excludePatterns || [''],
          });
          setActiveModels(bot.activeModels);
          setDefaultModel(bot.defaultModel);
        })
        .finally(() => {
          setIsLoading(false);
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isNewBot, botId]);

  const isSyncChunkError = useCallback((syncErrorMessage: string) => {
    const pattern =
      /Got a larger chunk overlap \(\d+\) than chunk size \(\d+\), should be smaller\./;
    return pattern.test(syncErrorMessage);
  }, []);

  const onChangeActiveModels = useCallback(
    (key: string, value: boolean) => {
      const camelKey = toCamelCase(key) as keyof ActiveModels;
      setActiveModels((prevState) => {
        const newActiveModels = { ...prevState, [camelKey]: value };
        if (!value && toCamelCase(defaultModel) === camelKey) {
          const fallback = activeModelsOptions.find(
            ({ key: optionKey }) =>
              newActiveModels[toCamelCase(optionKey) as keyof ActiveModels] !==
              false
          );
          if (fallback) {
            setDefaultModel(fallback.key);
          }
        }
        return newActiveModels;
      });
    },
    [defaultModel, activeModelsOptions]
  );

  const addQuickStarter = useCallback(() => {
    setConversationQuickStarters(
      produce(conversationQuickStarters, (draft) => {
        draft.push({
          title: '',
          example: '',
        });
      })
    );
  }, [conversationQuickStarters]);

  const updateQuickStarter = useCallback(
    (quickStart: ConversationQuickStarter, index: number) => {
      setConversationQuickStarters(
        produce(conversationQuickStarters, (draft) => {
          draft[index] = quickStart;
        })
      );
    },
    [conversationQuickStarters]
  );

  const removeQuickStarter = useCallback(
    (index: number) => {
      setConversationQuickStarters(
        produce(conversationQuickStarters, (draft) => {
          draft.splice(index, 1);
          if (draft.length === 0) {
            draft.push({
              title: '',
              example: '',
            });
          }
        })
      );
    },
    [conversationQuickStarters]
  );

  const onClickBack = useCallback(() => {
    history.back();
  }, []);

  const isValidGenerationConfigParam = useCallback(
    (value: number, key: 'maxTokens' | 'topK' | 'topP' | 'temperature') => {
      if (value < edgeGenerationParams[key].MIN) {
        setErrorMessages(
          key,
          t('validation.minRange.message', {
            size: edgeGenerationParams[key].MIN,
          })
        );
        return false;
      } else if (value > edgeGenerationParams[key].MAX) {
        setErrorMessages(
          key,
          t('validation.maxRange.message', {
            size: edgeGenerationParams[key].MAX,
          })
        );
        return false;
      }

      return true;
    },
    [setErrorMessages, t]
  );

  const isValidBudgetTokens = useCallback(
    (value: number) => {
      if (value < EDGE_GENERATION_PARAMS.budgetTokens.MIN) {
        setErrorMessages(
          'budgetTokens',
          t('validation.minRange.message', {
            size: EDGE_GENERATION_PARAMS.budgetTokens.MIN,
          })
        );
        return false;
      } else if (value > maxTokens) {
        setErrorMessages(
          'budgetTokens',
          t('validation.maxBudgetTokens.message', {
            size: maxTokens,
          })
        );
        return false;
      }
      return true;
    },
    [setErrorMessages, t, maxTokens]
  );
  const isToolValid = useCallback((): boolean => {
    clearErrorMessages();

    // Early return if no tools
    if (!tools.length) {
      return true;
    }

    // Use some() instead of every() since we want to find invalid tools
    const hasInvalidTool = tools.some((tool, idx) => {
      // BedrockAgentTool validation
      if (isBedrockAgentTool(tool) && !tool.bedrockAgentConfig?.agentId) {
        setErrorMessages(
          `tools-${idx}-bedrockAgentConfig.agent_id`,
          t('input.validationError.required')
        );
        return true;
      }

      if (isBedrockAgentTool(tool) && !tool.bedrockAgentConfig?.aliasId) {
        setErrorMessages(
          `tools-${idx}-bedrockAgentConfig.alias_id`,
          t('input.validationError.required')
        );
        return true;
      }

      // Firecrawl tool validation
      if (
        isInternetTool(tool) &&
        tool.searchEngine === 'firecrawl' &&
        (!tool.firecrawlConfig || !tool.firecrawlConfig.apiKey)
      ) {
        setErrorMessages(
          `tools-${idx}-firecrawlConfig.apiKey`,
          t('input.validationError.required')
        );
        return true;
      }

      // Mcp tool validation: every configured server must have all fields filled,
      // and labels must be unique within the bot.
      if (isMcpTool(tool)) {
        const labels = tool.mcpServers.map((server) => server.label);
        const hasDuplicateLabel = labels.some(
          (label, labelIdx) => label !== '' && labels.indexOf(label) !== labelIdx
        );

        if (hasDuplicateLabel) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.duplicateLabel')
          );
          return true;
        }

        const requiredFieldsByAuthType: Record<
          McpAuthType,
          (keyof McpConfigType)[]
        > = {
          cognito_client_credentials: ['clientId', 'clientSecret'],
          none: [],
          bearer_token: ['bearerToken'],
          basic_auth: ['username', 'basicAuthToken'],
          api_key: ['apiKey'],
          oauth: [],
        };

        const hasInvalidServer = tool.mcpServers.some(
          (server) =>
            !server.label ||
            !server.endpointUrl ||
            requiredFieldsByAuthType[server.authType].some(
              (field) => !server[field]
            )
        );

        if (hasInvalidServer) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.requiredFields')
          );
          return true;
        }

        const hasInvalidLabelPattern = tool.mcpServers.some(
          (server) => !/^[a-zA-Z0-9_]+$/.test(server.label)
        );

        if (hasInvalidLabelPattern) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.invalidLabelPattern')
          );
          return true;
        }

        const hasTooLongLabel = tool.mcpServers.some(
          (server) => server.label.length > 20
        );

        if (hasTooLongLabel) {
          setErrorMessages(
            `tools-${idx}-mcpServers`,
            t('agent.tools.mcp.error.labelTooLong')
          );
          return true;
        }
      }

      return false; // Tool is valid
    });

    return !hasInvalidTool;
  }, [clearErrorMessages, tools, setErrorMessages, t]);

  const isValid = useCallback((): boolean => {
    clearErrorMessages();

    // S3 URLs validation - s3://example-bucket/path/to/data-source/
    const isS3UrlsValid = s3Urls.every((url, idx) => {
      if (url && !/^s3:\/\/[a-z0-9.-]+\/.+/.test(url)) {
        setErrorMessages(`s3Urls-${idx}`, 'S3 URL is invalid');
        return false;
      } else {
        return true;
      }
    });
    if (!isS3UrlsValid) {
      return false;
    }

    if (!isToolValid()) {
      return false;
    }

    // Chunking Strategy params validation
    if (chunkingStrategy === 'fixed_size') {
      if (fixedSizeParams.maxTokens < EDGE_FIXED_CHUNK_PARAMS.maxTokens.MIN) {
        setErrorMessages(
          'fixedSizeParams.maxTokens',
          t('validation.minRange.message', {
            size: EDGE_FIXED_CHUNK_PARAMS.maxTokens.MIN,
          })
        );
        return false;
      } else if (
        fixedSizeParams.maxTokens >
        EDGE_FIXED_CHUNK_PARAMS.maxTokens.MAX[embeddingsModel]
      ) {
        setErrorMessages(
          'fixedSizeParams.maxTokens',
          t('validation.maxRange.message', {
            size: EDGE_FIXED_CHUNK_PARAMS.maxTokens.MAX[embeddingsModel],
          })
        );
        return false;
      }

      if (
        fixedSizeParams.overlapPercentage <
        EDGE_FIXED_CHUNK_PARAMS.overlapPercentage.MIN
      ) {
        setErrorMessages(
          'fixedSizeParams.overlapPercentage',
          t('validation.minRange.message', {
            size: EDGE_FIXED_CHUNK_PARAMS.overlapPercentage.MIN,
          })
        );
        return false;
      } else if (
        fixedSizeParams.overlapPercentage >
        EDGE_FIXED_CHUNK_PARAMS.overlapPercentage.MAX
      ) {
        setErrorMessages(
          'fixedSizeParams.overlapPercentage',
          t('validation.maxRange.message', {
            size: EDGE_FIXED_CHUNK_PARAMS.overlapPercentage.MAX,
          })
        );
        return false;
      }
    } else if (chunkingStrategy === 'hierarchical') {
      if (
        hierarchicalParams.overlapTokens <
        EDGE_HIERARCHICAL_CHUNK_PARAMS.overlapTokens.MIN
      ) {
        setErrorMessages(
          'hierarchicalParams.overlapTokens',
          t('validation.minRange.message', {
            size: EDGE_HIERARCHICAL_CHUNK_PARAMS.overlapTokens.MIN,
          })
        );
        return false;
      }

      if (
        hierarchicalParams.maxParentTokenSize <
        EDGE_HIERARCHICAL_CHUNK_PARAMS.maxParentTokenSize.MIN
      ) {
        setErrorMessages(
          'hierarchicalParams.maxParentTokenSize',
          t('validation.minRange.message', {
            size: EDGE_HIERARCHICAL_CHUNK_PARAMS.maxParentTokenSize.MIN,
          })
        );
        return false;
      } else if (
        hierarchicalParams.maxParentTokenSize >
        EDGE_HIERARCHICAL_CHUNK_PARAMS.maxParentTokenSize.MAX[embeddingsModel]
      ) {
        setErrorMessages(
          'hierarchicalParams.maxParentTokenSize',
          t('validation.maxRange.message', {
            size: EDGE_HIERARCHICAL_CHUNK_PARAMS.maxParentTokenSize.MAX[
              embeddingsModel
            ],
          })
        );
        return false;
      }

      if (
        hierarchicalParams.maxChildTokenSize <
        EDGE_HIERARCHICAL_CHUNK_PARAMS.maxChildTokenSize.MIN
      ) {
        setErrorMessages(
          'hierarchicalParams.maxChildTokenSize',
          t('validation.minRange.message', {
            size: EDGE_HIERARCHICAL_CHUNK_PARAMS.maxChildTokenSize.MIN,
          })
        );
        return false;
      } else if (
        hierarchicalParams.maxChildTokenSize >
        EDGE_HIERARCHICAL_CHUNK_PARAMS.maxChildTokenSize.MAX[embeddingsModel]
      ) {
        setErrorMessages(
          'hierarchicalParams.maxChildTokenSize',
          t('validation.maxRange.message', {
            size: EDGE_HIERARCHICAL_CHUNK_PARAMS.maxChildTokenSize.MAX[
              embeddingsModel
            ],
          })
        );
        return false;
      }

      if (
        hierarchicalParams.maxParentTokenSize <
        hierarchicalParams.maxChildTokenSize
      ) {
        setErrorMessages(
          'hierarchicalParams.maxParentTokenSize',
          t('validation.parentTokenRange.message')
        );
        return false;
      }
    } else if (chunkingStrategy === 'semantic') {
      if (semanticParams.maxTokens < EDGE_SEMANTIC_CHUNK_PARAMS.maxTokens.MIN) {
        setErrorMessages(
          'semanticParams.maxTokenss',
          t('validation.minRange.message', {
            size: EDGE_SEMANTIC_CHUNK_PARAMS.maxTokens.MIN,
          })
        );
        return false;
      } else if (
        semanticParams.maxTokens >
        EDGE_SEMANTIC_CHUNK_PARAMS.maxTokens.MAX[embeddingsModel]
      ) {
        setErrorMessages(
          'semanticParams.maxTokens',
          t('validation.maxRange.message', {
            size: EDGE_SEMANTIC_CHUNK_PARAMS.maxTokens.MAX[embeddingsModel],
          })
        );
        return false;
      }

      if (
        semanticParams.bufferSize < EDGE_SEMANTIC_CHUNK_PARAMS.bufferSize.MIN
      ) {
        setErrorMessages(
          'semanticParams.bufferSize',
          t('validation.minRange.message', {
            size: EDGE_SEMANTIC_CHUNK_PARAMS.bufferSize.MIN,
          })
        );
        return false;
      } else if (
        semanticParams.bufferSize > EDGE_SEMANTIC_CHUNK_PARAMS.bufferSize.MAX
      ) {
        setErrorMessages(
          'semanticParams.bufferSize',
          t('validation.maxRange.message', {
            size: EDGE_SEMANTIC_CHUNK_PARAMS.bufferSize.MAX,
          })
        );
        return false;
      }

      if (
        semanticParams.breakpointPercentileThreshold <
        EDGE_SEMANTIC_CHUNK_PARAMS.breakpointPercentileThreshold.MIN
      ) {
        setErrorMessages(
          'semanticParams.breakpointPercentileThreshold',
          t('validation.minRange.message', {
            size: EDGE_SEMANTIC_CHUNK_PARAMS.breakpointPercentileThreshold.MIN,
          })
        );
        return false;
      } else if (
        semanticParams.breakpointPercentileThreshold >
        EDGE_SEMANTIC_CHUNK_PARAMS.breakpointPercentileThreshold.MAX
      ) {
        setErrorMessages(
          'semanticParams.breakpointPercentileThreshold',
          t('validation.maxRange.message', {
            size: EDGE_SEMANTIC_CHUNK_PARAMS.breakpointPercentileThreshold.MAX,
          })
        );
        return false;
      }
    }

    if (searchParams.maxResults < EDGE_SEARCH_PARAMS.maxResults.MIN) {
      setErrorMessages(
        'maxResults',
        t('validation.minRange.message', {
          size: EDGE_SEARCH_PARAMS.maxResults.MIN,
        })
      );
      return false;
    } else if (searchParams.maxResults > EDGE_SEARCH_PARAMS.maxResults.MAX) {
      setErrorMessages(
        'maxResults',
        t('validation.maxRange.message', {
          size: EDGE_SEARCH_PARAMS.maxResults.MAX,
        })
      );
      return false;
    }

    const isQsValid = conversationQuickStarters.every((rs, idx) => {
      if ((!rs.title && !!rs.example) || (!!rs.title && !rs.example)) {
        setErrorMessages(
          `conversationQuickStarter${idx}`,
          t('validation.quickStarter.message')
        );
        return false;
      } else {
        return true;
      }
    });
    if (!isQsValid) {
      return false;
    }

    if (!isValidBudgetTokens(budgetTokens)) {
      return false;
    }

    return (
      isValidGenerationConfigParam(maxTokens, 'maxTokens') &&
      isValidGenerationConfigParam(topK, 'topK') &&
      isValidGenerationConfigParam(topP, 'topP') &&
      isValidGenerationConfigParam(temperature, 'temperature')
    );
  }, [
    clearErrorMessages,
    s3Urls,
    searchParams.maxResults,
    conversationQuickStarters,
    isToolValid,
    isValidGenerationConfigParam,
    budgetTokens,
    isValidBudgetTokens,
    maxTokens,
    topK,
    topP,
    temperature,
    setErrorMessages,
    embeddingsModel,
    chunkingStrategy,
    fixedSizeParams,
    hierarchicalParams,
    semanticParams,
    t,
  ]);

  const onClickCreate = useCallback(() => {
    if (!isValid()) {
      return;
    }
    setIsLoading(true);
    registerBot({
      agent: {
        tools,
      },
      id: botId,
      title,
      description,
      instruction,
      generationParams: {
        maxTokens,
        temperature,
        topK,
        topP,
        stopSequences: stopSequences.split(','),
        reasoningParams: {
          budgetTokens,
        },
      },
      knowledge: {
        sourceUrls: urls.filter((s) => s !== ''),
        // Sitemap cannot be used yet.
        sitemapUrls: [],
        s3Urls: s3Urls.filter((s) => s !== ''),
        filenames: files.map((f) => f.filename),
      },
      displayRetrievedChunks,
      promptCachingEnabled: promptCachingEnabled,
      conversationQuickStarters: conversationQuickStarters.filter(
        (qs) => qs.title !== '' && qs.example !== ''
      ),
      bedrockKnowledgeBase: {
        type: bedrockKnowledgeBaseType,
        knowledgeBaseId: null,
        existKnowledgeBaseId,
        embeddingsModel,
        chunkingConfiguration: (() => {
          switch (chunkingStrategy) {
            case 'default':
              return { chunkingStrategy: 'default' };
            case 'fixed_size':
              return fixedSizeParams;
            case 'hierarchical':
              return hierarchicalParams;
            case 'semantic':
              return semanticParams;
            default:
              return { chunkingStrategy: 'none' };
          }
        })(),
        openSearch: openSearchParams,
        searchParams: searchParams,
        parsingModel,
        webCrawlingScope,
        webCrawlingFilters,
      },
      bedrockGuardrails: {
        isGuardrailEnabled,
        hateThreshold: hateThreshold,
        insultsThreshold: insultsThreshold,
        sexualThreshold: sexualThreshold,
        violenceThreshold: violenceThreshold,
        misconductThreshold: misconductThreshold,
        groundingThreshold: groundingThreshold,
        relevanceThreshold: relevanceThreshold,
        guardrailArn: '',
        guardrailVersion: '',
      },
      activeModels,
      defaultModel,
    })
      .then(() => {
        navigate('/bot/my');
      })
      .catch(() => {
        setIsLoading(false);
      });
  }, [
    isValid,
    registerBot,
    tools,
    botId,
    title,
    description,
    instruction,
    maxTokens,
    temperature,
    topK,
    topP,
    stopSequences,
    budgetTokens,
    searchParams,
    urls,
    s3Urls,
    files,
    displayRetrievedChunks,
    promptCachingEnabled,
    conversationQuickStarters,
    navigate,
    bedrockKnowledgeBaseType,
    existKnowledgeBaseId,
    embeddingsModel,
    chunkingStrategy,
    fixedSizeParams,
    hierarchicalParams,
    semanticParams,
    openSearchParams,
    isGuardrailEnabled,
    hateThreshold,
    insultsThreshold,
    sexualThreshold,
    violenceThreshold,
    misconductThreshold,
    groundingThreshold,
    relevanceThreshold,
    parsingModel,
    webCrawlingScope,
    webCrawlingFilters,
    activeModels,
    defaultModel,
  ]);

  const onClickEdit = useCallback(() => {
    if (!isValid()) {
      return;
    }
    if (!isNewBot) {
      setIsLoading(true);
      updateBot(botId, {
        agent: {
          tools,
        },
        title,
        description,
        instruction,
        generationParams: {
          maxTokens,
          temperature,
          topK,
          topP,
          stopSequences: stopSequences.split(','),
          reasoningParams: {
            budgetTokens,
          },
        },
        knowledge: {
          sourceUrls: urls.filter((s) => s !== ''),
          // Sitemap cannot be used yet.
          sitemapUrls: [],
          s3Urls: s3Urls.filter((s) => s !== ''),
          addedFilenames: [],
          deletedFilenames: [],
          unchangedFilenames,
        },
        displayRetrievedChunks,
        promptCachingEnabled: promptCachingEnabled,
        conversationQuickStarters: conversationQuickStarters.filter(
          (qs) => qs.title !== '' && qs.example !== ''
        ),
        bedrockKnowledgeBase: {
          type: bedrockKnowledgeBaseType,
          knowledgeBaseId: (bedrockKnowledgeBaseType != null) ? knowledgeBaseId : null,
          existKnowledgeBaseId,
          embeddingsModel,
          chunkingConfiguration: (() => {
            switch (chunkingStrategy) {
              case 'default':
                return { chunkingStrategy: 'default' };
              case 'fixed_size':
                return fixedSizeParams;
              case 'hierarchical':
                return hierarchicalParams;
              case 'semantic':
                return semanticParams;
              default:
                return { chunkingStrategy: 'none' };
            }
          })(),
          openSearch: openSearchParams,
          searchParams: searchParams,
          parsingModel,
          webCrawlingScope,
          webCrawlingFilters,
        },
        bedrockGuardrails: {
          isGuardrailEnabled,
          hateThreshold: hateThreshold,
          insultsThreshold: insultsThreshold,
          sexualThreshold: sexualThreshold,
          violenceThreshold: violenceThreshold,
          misconductThreshold: misconductThreshold,
          groundingThreshold: groundingThreshold,
          relevanceThreshold: relevanceThreshold,
          guardrailArn: (isGuardrailEnabled) ? guardrailArn : '',
          guardrailVersion: (isGuardrailEnabled) ? guardrailVersion : '',
        },
        activeModels,
        defaultModel,
      })
        .then(() => {
          navigate('/bot/my');
        })
        .catch(() => {
          setIsLoading(false);
        });
    }
  }, [
    isValid,
    isNewBot,
    updateBot,
    botId,
    tools,
    title,
    description,
    instruction,
    maxTokens,
    temperature,
    topK,
    topP,
    stopSequences,
    budgetTokens,
    searchParams,
    urls,
    s3Urls,
    unchangedFilenames,
    displayRetrievedChunks,
    promptCachingEnabled,
    conversationQuickStarters,
    navigate,
    bedrockKnowledgeBaseType,
    knowledgeBaseId,
    existKnowledgeBaseId,
    embeddingsModel,
    chunkingStrategy,
    fixedSizeParams,
    hierarchicalParams,
    semanticParams,
    openSearchParams,
    isGuardrailEnabled,
    hateThreshold,
    insultsThreshold,
    sexualThreshold,
    violenceThreshold,
    misconductThreshold,
    groundingThreshold,
    relevanceThreshold,
    guardrailArn,
    guardrailVersion,
    parsingModel,
    webCrawlingScope,
    webCrawlingFilters,
    activeModels,
    defaultModel,
  ]);

  const [isOpenSamples, setIsOpenSamples] = useState(false);

  const disabledRegister = useMemo(() => {
    return title === '' || files.findIndex((f) => f.status !== 'UPLOADED') > -1;
  }, [files, title]);

  return (
    <>
      <DialogInstructionsSamples
        isOpen={isOpenSamples}
        onClose={() => {
          setIsOpenSamples(false);
        }}
      />
      <div className="mb-20 flex justify-center">
        <div className="w-2/3">
          <div className="mt-5 w-full">
            <div className="text-xl font-bold">
              {isNewBot ? t('bot.create.pageTitle') : t('bot.edit.pageTitle')}
            </div>

            <div className="mt-3 flex flex-col gap-3">
              <InputText
                label={t('bot.item.title')}
                disabled={isLoading}
                value={title}
                onChange={setTitle}
                hint={t('input.hint.required')}
              />
              <InputText
                label={t('bot.item.description')}
                disabled={isLoading}
                value={description}
                onChange={setDescription}
              />
              <div className="relative mt-3">
                <Button
                  className="absolute -top-3 right-0 text-xs"
                  outlined
                  onClick={() => {
                    setIsOpenSamples(true);
                  }}>
                  <PiNote className="mr-1" />
                  {t('bot.button.instructionsSamples')}
                </Button>
                <Textarea
                  label={t('bot.item.instruction')}
                  disabled={isLoading}
                  rows={5}
                  hint={t('bot.help.instructions')}
                  value={instruction}
                  onChange={setInstruction}
                />
              </div>

              <div className="mt-3" />
              <AvailableTools
                availableTools={availableTools}
                tools={tools}
                savedTools={savedTools}
                setTools={setTools}
                errorMessages={errorMessages}
                botId={botId}
                isNewBot={isNewBot}
              />

              <div className="mt-3">
                <div className="flex items-center gap-1">
                  <div className="text-lg font-bold">
                    {t('bot.label.quickStarter.title')}
                  </div>
                </div>

                <div className="text-sm text-aws-font-color-light/50 dark:text-aws-font-color-dark">
                  {t('bot.help.quickStarter.overview')}
                </div>

                <div className="mt-2">
                  <div className="mt-2 flex w-full flex-col gap-1">
                    {conversationQuickStarters.map(
                      (conversationQuickStarter, idx) => (
                        <div
                          className="flex w-full flex-col gap-2 rounded border border-aws-font-color-light/50 p-2 dark:border-aws-font-color-dark/50"
                          key={idx}>
                          <InputText
                            className="w-full"
                            placeholder={t(
                              'bot.label.quickStarter.exampleTitle'
                            )}
                            disabled={isLoading}
                            value={conversationQuickStarter.title}
                            onChange={(s) => {
                              updateQuickStarter(
                                {
                                  ...conversationQuickStarter,
                                  title: s,
                                },
                                idx
                              );
                            }}
                            errorMessage={
                              errorMessages[`conversationQuickStarter${idx}`]
                            }
                          />

                          <Textarea
                            className="w-full"
                            label={t('bot.label.quickStarter.example')}
                            disabled={isLoading}
                            rows={3}
                            value={conversationQuickStarter.example}
                            onChange={(s) => {
                              updateQuickStarter(
                                {
                                  ...conversationQuickStarter,
                                  example: s,
                                },
                                idx
                              );
                            }}
                          />
                          <div className="flex justify-end">
                            <Button
                              className="bg-red"
                              disabled={
                                (conversationQuickStarters.length === 1 &&
                                  !conversationQuickStarters[0].title &&
                                  !conversationQuickStarters[0].example) ||
                                isLoading
                              }
                              icon={<PiTrash />}
                              onClick={() => {
                                removeQuickStarter(idx);
                              }}>
                              {t('button.delete')}
                            </Button>
                          </div>
                        </div>
                      )
                    )}
                  </div>
                  <div className="mt-2">
                    <Button
                      outlined
                      icon={<PiPlus />}
                      onClick={addQuickStarter}>
                      {t('button.add')}
                    </Button>
                  </div>
                </div>
              </div>

              <ExpandableDrawerGroup
                isDefaultShow={false}
                label={t('generationConfig.title')}
                className="py-2">
                <GenerationConfig
                  topK={topK}
                  setTopK={setTopK}
                  topP={topP}
                  setTopP={setTopP}
                  temperature={temperature}
                  setTemperature={setTemperature}
                  maxTokens={maxTokens}
                  setMaxTokens={setMaxTokens}
                  stopSequences={stopSequences}
                  setStopSequences={setStopSequences}
                  budgetTokens={budgetTokens}
                  setBudgetTokens={setBudgetTokens}
                  isLoading={isLoading}
                  errorMessages={errorMessages}
                />
              </ExpandableDrawerGroup>

              <ExpandableDrawerGroup
                isDefaultShow={false}
                label={t('guardrails.harmfulCategories.label')}
                className="py-2">
                <div className="mt-2">
                  <Slider
                    value={hateThreshold}
                    hint={t('guardrails.harmfulCategories.hate.hint')}
                    label={t('guardrails.harmfulCategories.hate.label')}
                    range={{
                      min: GUARDRAILS_FILTERS_THRESHOLD.MIN,
                      max: GUARDRAILS_FILTERS_THRESHOLD.MAX,
                      step: GUARDRAILS_FILTERS_THRESHOLD.STEP,
                    }}
                    onChange={(hateThreshold) => {
                      setHateThreshold(hateThreshold);
                    }}
                    enableDecimal={true}
                    errorMessage={errorMessages['hateThreshold']}
                  />
                </div>
                <div className="mt-2">
                  <Slider
                    value={insultsThreshold}
                    hint={t('guardrails.harmfulCategories.insults.hint')}
                    label={t('guardrails.harmfulCategories.insults.label')}
                    range={{
                      min: GUARDRAILS_FILTERS_THRESHOLD.MIN,
                      max: GUARDRAILS_FILTERS_THRESHOLD.MAX,
                      step: GUARDRAILS_FILTERS_THRESHOLD.STEP,
                    }}
                    onChange={(insultsThreshold) => {
                      setInsultsThreshold(insultsThreshold);
                    }}
                    enableDecimal={true}
                    errorMessage={errorMessages['insultsThreshold']}
                  />
                </div>
                <div className="mt-2">
                  <Slider
                    value={sexualThreshold}
                    hint={t('guardrails.harmfulCategories.sexual.hint')}
                    label={t('guardrails.harmfulCategories.sexual.label')}
                    range={{
                      min: GUARDRAILS_FILTERS_THRESHOLD.MIN,
                      max: GUARDRAILS_FILTERS_THRESHOLD.MAX,
                      step: GUARDRAILS_FILTERS_THRESHOLD.STEP,
                    }}
                    onChange={(sexualThreshold) => {
                      setSexualThreshold(sexualThreshold);
                    }}
                    enableDecimal={true}
                    errorMessage={errorMessages['sexualThreshold']}
                  />
                </div>
                <div className="mt-2">
                  <Slider
                    value={violenceThreshold}
                    hint={t('guardrails.harmfulCategories.violence.hint')}
                    label={t('guardrails.harmfulCategories.violence.label')}
                    range={{
                      min: GUARDRAILS_FILTERS_THRESHOLD.MIN,
                      max: GUARDRAILS_FILTERS_THRESHOLD.MAX,
                      step: GUARDRAILS_FILTERS_THRESHOLD.STEP,
                    }}
                    onChange={(violenceThreshold) => {
                      setViolenceThreshold(violenceThreshold);
                    }}
                    enableDecimal={true}
                    errorMessage={errorMessages['violenceThreshold']}
                  />
                </div>
                <div className="mt-2">
                  <Slider
                    value={misconductThreshold}
                    hint={t('guardrails.harmfulCategories.misconduct.hint')}
                    label={t('guardrails.harmfulCategories.misconduct.label')}
                    range={{
                      min: GUARDRAILS_FILTERS_THRESHOLD.MIN,
                      max: GUARDRAILS_FILTERS_THRESHOLD.MAX,
                      step: GUARDRAILS_FILTERS_THRESHOLD.STEP,
                    }}
                    onChange={(misconductThreshold) => {
                      setMisconductThreshold(misconductThreshold);
                    }}
                    enableDecimal={true}
                    errorMessage={errorMessages['misconductThreshold']}
                  />
                </div>
              </ExpandableDrawerGroup>

              <ExpandableDrawerGroup
                isDefaultShow={false}
                label={t('guardrails.contextualGroundingCheck.label')}
                className="py-2">
                <div className="mt-2">
                  <Slider
                    value={groundingThreshold}
                    hint={t(
                      'guardrails.contextualGroundingCheck.groundingThreshold.hint'
                    )}
                    label={t(
                      'guardrails.contextualGroundingCheck.groundingThreshold.label'
                    )}
                    range={{
                      min: GUARDRAILS_CONTEXTUAL_GROUNDING_THRESHOLD.MIN,
                      max: GUARDRAILS_CONTEXTUAL_GROUNDING_THRESHOLD.MAX,
                      step: GUARDRAILS_CONTEXTUAL_GROUNDING_THRESHOLD.STEP,
                    }}
                    onChange={(groundingThreshold) => {
                      setGroundingThreshold(groundingThreshold);
                    }}
                    enableDecimal={true}
                    errorMessage={errorMessages['groundingThreshold']}
                  />
                </div>
                <div className="mt-2">
                  <Slider
                    value={relevanceThreshold}
                    hint={t(
                      'guardrails.contextualGroundingCheck.relevanceThreshold.hint'
                    )}
                    label={t(
                      'guardrails.contextualGroundingCheck.relevanceThreshold.label'
                    )}
                    range={{
                      min: GUARDRAILS_CONTEXTUAL_GROUNDING_THRESHOLD.MIN,
                      max: GUARDRAILS_CONTEXTUAL_GROUNDING_THRESHOLD.MAX,
                      step: GUARDRAILS_CONTEXTUAL_GROUNDING_THRESHOLD.STEP,
                    }}
                    onChange={(relevanceThreshold) => {
                      setRelevanceThreshold(relevanceThreshold);
                    }}
                    enableDecimal={true}
                    errorMessage={errorMessages['relevanceThreshold']}
                  />
                </div>
              </ExpandableDrawerGroup>

              <div className="mt-3">
                <Select
                  label={t('bot.defaultModel.title')}
                  value={defaultModel}
                  options={activeModelsOptions
                    .filter(
                      ({ key }) =>
                        activeModels[toCamelCase(key) as keyof ActiveModels] !==
                        false
                    )
                    .map(({ key, label }) => ({
                      value: key,
                      label,
                    }))}
                  onChange={(val) => {
                    setDefaultModel(val as Model);
                  }}
                />
                <div className="text-sm text-aws-font-color-light/50 dark:text-aws-font-color-dark">
                  {t('bot.defaultModel.description')}
                </div>
              </div>

              <ExpandableDrawerGroup
                isDefaultShow={false}
                label={t('bot.activeModels.title')}
                className="py-2">
                <div className="text-sm text-aws-font-color-light/50 dark:text-aws-font-color-dark">
                  {t('bot.activeModels.description')}
                </div>

                <div className="mt-4">
                  <div className="mt-2 space-y-2">
                    {activeModelsOptions.map(({ key, label, description }) => (
                      <div key={key} className="flex items-start">
                        <Toggle
                          value={
                            activeModels[
                              toCamelCase(key) as keyof ActiveModels
                            ] ?? true
                          }
                          onChange={(value) => onChangeActiveModels(key, value)}
                        />
                        <div>
                          <div>{label}</div>
                          <div className="text-sm text-dark-gray dark:text-light-gray">
                            {description}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </ExpandableDrawerGroup>

              <ExpandableDrawerGroup
                isDefaultShow={false}
                label={t('bot.promptCaching.title')}
                className="py-2"
              >
                <div className="flex mt-4 items-start">
                  <Toggle
                    value={promptCachingEnabled}
                    onChange={setPromptCachingEnabled}
                  />
                  <div>
                    <Trans t={t} i18nKey="bot.promptCaching.promptCachingEnabled.title" />
                    <div className="text-sm text-dark-gray dark:text-light-gray">
                      <Trans t={t} i18nKey="bot.promptCaching.promptCachingEnabled.description" />
                    </div>
                  </div>
                </div>
              </ExpandableDrawerGroup>

              {errorMessages['syncChunkError'] && (
                <Alert
                  className="mt-2"
                  severity="error"
                  title={t('embeddingSettings.alert.sync.error.title')}>
                  <>
                    <div className="mb-1 text-sm">
                      {t('embeddingSettings.alert.sync.error.body')}
                    </div>
                  </>
                </Alert>
              )}

              <div className="flex justify-between">
                <Button outlined icon={<PiCaretLeft />} onClick={onClickBack}>
                  {t('button.back')}
                </Button>

                {isNewBot ? (
                  <Button
                    onClick={onClickCreate}
                    loading={isLoading}
                    disabled={disabledRegister}>
                    {t('bot.button.create')}
                  </Button>
                ) : (
                  <Button
                    onClick={onClickEdit}
                    loading={isLoading}
                    disabled={disabledRegister}>
                    {t('bot.button.save')}
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default BotKbEditPage;
