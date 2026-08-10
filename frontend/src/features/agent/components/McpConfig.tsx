import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import InputText from '../../../components/InputText';
import Select from '../../../components/Select';
import Button from '../../../components/Button';
import useBotApi from '../../../hooks/useBotApi';
import useSnackbar from '../../../hooks/useSnackbar';
import { McpAuthType, McpConfig as McpConfigType } from '../types';

type Props = {
  config: McpConfigType;
  savedConfig?: McpConfigType;
  onChange: (config: McpConfigType) => void;
  botId: string;
  isNewBot: boolean;
};

const AUTH_TYPES: McpAuthType[] = [
  'cognito_client_credentials',
  'none',
  'bearer_token',
  'basic_auth',
  'api_key',
  'oauth',
];

export const McpConfig = ({
  config,
  savedConfig,
  onChange,
  botId,
  isNewBot,
}: Props) => {
  const { t } = useTranslation();
  const { postMcpOauthAuthorize, deleteMcpOauth } = useBotApi();
  const snackbar = useSnackbar();
  const [isConnecting, setIsConnecting] = useState(false);

  const authTypeOptions = AUTH_TYPES.map((authType) => ({
    value: authType,
    label: t(`agent.tools.mcpConfig.authType.options.${authType}`),
  }));

  const handleConnect = () => {
    setIsConnecting(true);
    postMcpOauthAuthorize(botId, config.label)
      .then(({ authorizationUrl }) => {
        window.location.href = authorizationUrl;
      })
      .catch(() => {
        snackbar.open(t('agent.tools.mcpConfig.oauth.connectError'));
        setIsConnecting(false);
      });
  };

  const handleDisconnect = () => {
    deleteMcpOauth(botId, config.label).then(() =>
      onChange({ ...config, oauthConnected: false })
    );
  };

  return (
    <div className="space-y-4">
      <InputText
        label={t('agent.tools.mcpConfig.label.label')}
        placeholder={t('agent.tools.mcpConfig.label.placeholder')}
        value={config.label}
        maxLength={20}
        onChange={(value) => onChange({ ...config, label: value })}
      />
      <InputText
        label={t('agent.tools.mcpConfig.endpointUrl.label')}
        placeholder={t('agent.tools.mcpConfig.endpointUrl.placeholder')}
        value={config.endpointUrl}
        onChange={(value) => onChange({ ...config, endpointUrl: value })}
      />
      <Select
        label={t('agent.tools.mcpConfig.authType.label')}
        value={config.authType}
        options={authTypeOptions}
        onChange={(value) =>
          onChange({
            ...config,
            authType: value as McpAuthType,
            clientId: undefined,
            clientSecret: undefined,
            bearerToken: undefined,
            username: undefined,
            basicAuthToken: undefined,
            apiKey: undefined,
          })
        }
      />
      {config.authType === 'cognito_client_credentials' && (
        <>
          <InputText
            label={t('agent.tools.mcpConfig.clientId.label')}
            placeholder={t('agent.tools.mcpConfig.clientId.placeholder')}
            value={config.clientId ?? ''}
            onChange={(value) => onChange({ ...config, clientId: value })}
          />
          <InputText
            type="password"
            label={t('agent.tools.mcpConfig.clientSecret.label')}
            placeholder={t('agent.tools.mcpConfig.clientSecret.placeholder')}
            value={config.clientSecret ?? ''}
            onChange={(value) => onChange({ ...config, clientSecret: value })}
          />
        </>
      )}
      {config.authType === 'bearer_token' && (
        <InputText
          type="password"
          label={t('agent.tools.mcpConfig.bearerToken.label')}
          placeholder={t('agent.tools.mcpConfig.bearerToken.placeholder')}
          value={config.bearerToken ?? ''}
          onChange={(value) => onChange({ ...config, bearerToken: value })}
        />
      )}
      {config.authType === 'basic_auth' && (
        <>
          <InputText
            label={t('agent.tools.mcpConfig.username.label')}
            placeholder={t('agent.tools.mcpConfig.username.placeholder')}
            value={config.username ?? ''}
            onChange={(value) => onChange({ ...config, username: value })}
          />
          <InputText
            type="password"
            label={t('agent.tools.mcpConfig.basicAuthToken.label')}
            placeholder={t('agent.tools.mcpConfig.basicAuthToken.placeholder')}
            value={config.basicAuthToken ?? ''}
            onChange={(value) => onChange({ ...config, basicAuthToken: value })}
          />
        </>
      )}
      {config.authType === 'api_key' && (
        <InputText
          type="password"
          label={t('agent.tools.mcpConfig.apiKey.label')}
          placeholder={t('agent.tools.mcpConfig.apiKey.placeholder')}
          value={config.apiKey ?? ''}
          onChange={(value) => onChange({ ...config, apiKey: value })}
        />
      )}
      {config.authType === 'oauth' && (
        <div className="flex items-center gap-2">
          {isNewBot || savedConfig?.authType !== 'oauth' ? (
            <div className="text-sm text-aws-font-color-gray">
              {t('agent.tools.mcpConfig.oauth.saveFirst')}
            </div>
          ) : config.oauthConnected ? (
            <>
              <span className="text-sm text-green-700">
                {t('agent.tools.mcpConfig.oauth.connected')}
              </span>
              <Button outlined onClick={handleDisconnect}>
                {t('agent.tools.mcpConfig.oauth.disconnect')}
              </Button>
            </>
          ) : (
            <Button outlined loading={isConnecting} onClick={handleConnect}>
              {t('agent.tools.mcpConfig.oauth.connect')}
            </Button>
          )}
        </div>
      )}
    </div>
  );
};
