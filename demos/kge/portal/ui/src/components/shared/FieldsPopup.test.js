import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import FieldsPopup from './FieldsPopup';

test('shows the field overview and profile, and closes', () => {
  const close = vi.fn();
  render(<FieldsPopup
    rowData={{ 'Business Name': 'Order id', Description: 'Unique order key', 'Data Type': 'STRING' }}
    fieldPopupData={{ TechnicalName: 'order_id', DataClassification: 'Internal', APPElement: 'E1', SensitiveData: 'No', NullCount: 0, NullPercentage: '0%', DistinctCount: 42, DiscountPercentage: '100%', MinValue: 'A1', MaxValue: 'Z9', MinLength: 2, MaxLength: 8 }}
    popupState={{ close }}
  />);
  expect(screen.getByText('Field Overview')).toBeInTheDocument();
  for (const text of ['Order id', 'order_id', 'Unique order key', 'STRING', 'Internal', 'E1', 'No', '0%', '42', '100%', 'A1', 'Z9', '8']) {
    expect(screen.getByText(text)).toBeInTheDocument();
  }
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(close).toHaveBeenCalled();
});
