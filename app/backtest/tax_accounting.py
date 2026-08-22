from dataclasses import dataclass


TAX_ACCOUNTING_VERSION = "tax-accounting-pretax-v1"


@dataclass(frozen=True)
class TaxAccountingPolicy:
    """Versioned tax boundary for virtual-account results.

    The current engine intentionally reports pretax performance only.  It does
    not approximate account-specific taxation, loss offsetting, withholding,
    or tax filing.  A future tax implementation must use a different policy
    version after those requirements have been approved.
    """

    version: str = TAX_ACCOUNTING_VERSION
    evaluation_basis: str = "pretax"
    capital_gains_tax_rate: float = 0.0
    dividend_tax_rate: float = 0.0
    account_type: str = "not_modeled"
    loss_offsetting: str = "not_modeled"
    withholding: str = "not_modeled"
    tax_model_status: str = "not_implemented"

    def __post_init__(self) -> None:
        if self.version != TAX_ACCOUNTING_VERSION:
            raise ValueError("unsupported tax accounting policy version")
        if self.evaluation_basis != "pretax":
            raise ValueError("the current tax policy supports pretax evaluation only")
        if self.capital_gains_tax_rate != 0 or self.dividend_tax_rate != 0:
            raise ValueError("the current pretax policy requires zero tax rates")
        if self.account_type != "not_modeled":
            raise ValueError("account-specific taxation is not modeled")
        if self.loss_offsetting != "not_modeled":
            raise ValueError("loss offsetting is not modeled")
        if self.withholding != "not_modeled":
            raise ValueError("tax withholding is not modeled")
        if self.tax_model_status != "not_implemented":
            raise ValueError("the current tax model status must be not_implemented")

    def disclosure(self) -> dict:
        return {
            "version": self.version,
            "evaluation_basis": self.evaluation_basis,
            "capital_gains_tax_rate": self.capital_gains_tax_rate,
            "dividend_tax_rate": self.dividend_tax_rate,
            "estimated_tax": 0.0,
            "account_type": self.account_type,
            "loss_offsetting": self.loss_offsetting,
            "withholding": self.withholding,
            "tax_model_status": self.tax_model_status,
            "warning": (
                "税率0%の税引前評価です。口座種別、源泉徴収、損益通算、"
                "繰越控除、申告税務を再現していません。"
            ),
        }
