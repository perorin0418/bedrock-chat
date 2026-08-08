import { useTranslation } from 'react-i18next';
import InputText from '../../../components/InputText';
import Select from '../../../components/Select';
import { McpAuthType, McpConfig as McpConfigType } from '../types';

type Props = {
  config: McpConfigType;
  onChange: (config: McpConfigType) => void;
};

const AUTH_TYPES: McpAuthType[] = [
  'cognito_client_credentials',
  'none',
  'bearer_token',
  'basic_auth',
];

export const McpConfig = ({ config, onChange }: Props) => {
  const { t } = useTranslation();

  const authTypeOptions = AUTH_TYPES.map((authType) => ({
    value: authType,
    label: t(`agent.tools.mcpConfig.authType.options.${authType}`),
  }));

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
    </div>
  );
};
