import { useTranslation } from 'react-i18next';
import { McpConfig as McpConfigType } from '../types';
import { McpConfig as McpConfigComponent } from './McpConfig';
import Button from '../../../components/Button';
import Alert from '../../../components/Alert';

type Props = {
  servers: McpConfigType[];
  onChange: (servers: McpConfigType[]) => void;
  errorMessage?: string;
};

const EMPTY_SERVER: McpConfigType = {
  label: '',
  endpointUrl: '',
  authType: 'cognito_client_credentials',
};

export const McpServersConfig = ({ servers, onChange, errorMessage }: Props) => {
  const { t } = useTranslation();

  const handleServerChange = (index: number, config: McpConfigType) => {
    onChange(servers.map((server, i) => (i === index ? config : server)));
  };

  const handleRemoveServer = (index: number) => {
    onChange(servers.filter((_, i) => i !== index));
  };

  const handleAddServer = () => {
    onChange([...servers, { ...EMPTY_SERVER }]);
  };

  return (
    <div className="space-y-4">
      {errorMessage && (
        <Alert severity="error">
          <div className="text-sm">{errorMessage}</div>
        </Alert>
      )}
      {servers.map((server, index) => (
        <div
          key={index}
          className="flex items-start gap-2 border-b border-aws-font-color-gray/30 pb-4">
          <div className="flex-1">
            <McpConfigComponent
              config={server}
              onChange={(config) => handleServerChange(index, config)}
            />
          </div>
          <Button
            outlined
            className="mt-1"
            onClick={() => handleRemoveServer(index)}>
            {t('agent.tools.mcp.removeServer')}
          </Button>
        </div>
      ))}
      <Button outlined onClick={handleAddServer}>
        {t('agent.tools.mcp.addServer')}
      </Button>
    </div>
  );
};
