import { Form, Input, Modal, notification } from 'antd';
import { DislikeOutlined, LikeOutlined } from "@ant-design/icons";

const FeedbackForm = ({ open, onCreate, onCancel, chatFeedback, userQuerySeqId, handleStopClosingChatClick, confirmLoading }) => {
  const [form] = Form.useForm();
  const [api, contextHolder] = notification.useNotification();

  return (
    <div onMouseDown={handleStopClosingChatClick}>
        <Modal
        open={open}
        wrapClassName="v3-feedback-modal"
        okButtonProps={{ style: { backgroundColor: 'var(--v3-action)', color: 'var(--v3-action-ink)' } }}
        title="Feedback"
        okText="Submit"
        cancelText="Cancel"
        onCancel={onCancel}
        zIndex="1500"
        confirmLoading={confirmLoading}
        onOk={() => {
            form
            .validateFields()
            .then((values) => {
                form.resetFields();
                onCreate(values, chatFeedback, userQuerySeqId);

                if (chatFeedback === 'like') {
                    api.open({
                        message: 'Feedback Captured',
                        duration: 2,
                        icon: <LikeOutlined style={{ color: 'var(--v3-accent)' }} />,
                        placement: 'top'
                    });
                } else {
                    api.open({
                        message: 'Feedback Captured',
                        duration: 2,
                        icon: <DislikeOutlined style={{ color: 'var(--v3-accent)' }} />,
                        placement: 'top'
                    });
                }
            })
            .catch((info) => {
                console.log('Validate Failed:', info);
            });
        }}
        >
            {contextHolder}
            <Form
                form={form}
                layout="vertical"
                name="form_in_modal"
                initialValues={{
                    modifier: 'public',
                }}
            >
                { chatFeedback === 'dislike' ? (<Form.Item
                    name="feedback"
                    label="Your Feedback:"
                    rules={[
                        {
                        required: true,
                        message: 'Required Input',
                        },
                    ]}
                >
                    <Input.TextArea rows={4} />
                </Form.Item>) : (<Form.Item
                    name="feedback"
                    label="Your Feedback:"
                >
                    <Input.TextArea rows={4} />
                </Form.Item>) } 
            </Form>
        </Modal>
    </div>
  );
};

export default FeedbackForm;