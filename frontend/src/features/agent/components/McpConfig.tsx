import { useTranslation } from 'react-i18next';
import InputText from '../../../components/InputText';
import { McpConfig as McpConfigType } from '../types';

type Props = {
  config: McpConfigType;
  onChange: (config: McpConfigType) => void;
};

export const McpConfig = ({ config, onChange }: Props) => {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      <InputText
        label={t('agent.tools.mcpConfig.endpointUrl.label')}
        placeholder={t('agent.tools.mcpConfig.endpointUrl.placeholder')}
        value={config.endpointUrl}
        onChange={(value) => onChange({ ...config, endpointUrl: value })}
      />
      <InputText
        label={t('agent.tools.mcpConfig.clientId.label')}
        placeholder={t('agent.tools.mcpConfig.clientId.placeholder')}
        value={config.clientId}
        onChange={(value) => onChange({ ...config, clientId: value })}
      />
      <InputText
        type="password"
        label={t('agent.tools.mcpConfig.clientSecret.label')}
        placeholder={t('agent.tools.mcpConfig.clientSecret.placeholder')}
        value={config.clientSecret}
        onChange={(value) => onChange({ ...config, clientSecret: value })}
      />
    </div>
  );
};
