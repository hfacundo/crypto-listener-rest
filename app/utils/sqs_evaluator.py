# app/utils/sqs_evaluator.py
import json
import logging
from typing import Dict, Tuple
from app.utils.db.query_executor import get_rules

logger = logging.getLogger(__name__)

class SQSEvaluator:
    def __init__(self, user_id: str, strategy: str):
        self.user_id = user_id
        self.strategy = strategy
        self._config = None
        self._current_symbol = None
        self._load_config()

    def _load_config(self):
        """Load SQS configuration from database"""
        try:
            config_json = get_rules(self.user_id, self.strategy).get("sqs_config")
            if config_json:
                self._config = json.loads(config_json)
                logger.info(f"📊 SQS config loaded for {self.user_id}/{self.strategy}")
            else:
                logger.warning(f"⚠️ No SQS config found for {self.user_id}/{self.strategy}")
                self._config = self._get_default_config()
        except Exception as e:
            logger.error(f"❌ Error loading SQS config: {e}")
            self._config = self._get_default_config()

    def _get_default_config(self) -> Dict:
        """Fallback configuration if database is unavailable"""
        return {
            "enabled": True,
            "absolute_minimums": {"min_probability": 55, "min_sqs": 30},
            "probability_tiers": [
                {"min_probability": 55, "min_sqs": 30, "capital_multiplier": 1.0, "description": "Default"}
            ]
        }

    def evaluate_trade(self, probability: float, sqs: float, rr_ratio: float = 1.0, symbol: str = "UNKNOWN") -> Dict:
        """
        Evaluate trade and return decision with detailed breakdown

        Args:
            probability: Trade probability (55-65 typical range)
            sqs: Signal Quality Score (0-100)
            rr_ratio: Risk-reward ratio
            symbol: Trading symbol for logging

        Returns:
            {
                'action': 'accept'|'reject',
                'capital_multiplier': float,
                'quality_grade': str,
                'frequency_class': str,
                'reason': str,
                'breakdown': dict
            }
        """
        self._current_symbol = symbol

        if not self._config or not self._config.get("enabled", False):
            result = self._default_decision(probability, sqs)
            self._print_decision(probability, sqs, result, rr_ratio)
            return result

        # 1. Check absolute minimums
        min_prob = self._config["absolute_minimums"]["min_probability"]
        min_sqs = self._config["absolute_minimums"]["min_sqs"]

        if probability < min_prob or sqs < min_sqs:
            result = {
                'action': 'reject',
                'capital_multiplier': 0.0,
                'quality_grade': 'REJECTED',
                'frequency_class': 'FILTERED_OUT',
                'reason': f'Below minimums: prob {probability}% < {min_prob}% OR sqs {sqs} < {min_sqs}',
                'breakdown': self._create_breakdown(probability, sqs, rejected=True)
            }
            self._print_decision(probability, sqs, result, rr_ratio)
            return result

        # 2. Find best matching tier
        best_match = self._find_best_tier_match(probability, sqs)

        # 3. Apply special bonuses
        final_multiplier = self._apply_bonuses(probability, sqs, best_match['capital_multiplier'])

        # 4. Classify quality and frequency
        quality_grade = self._classify_quality(probability, sqs)
        frequency_class = self._classify_frequency(probability, sqs)

        result = {
            'action': 'accept',
            'capital_multiplier': final_multiplier,
            'quality_grade': quality_grade,
            'frequency_class': frequency_class,
            'reason': best_match['description'],
            'breakdown': self._create_breakdown(probability, sqs, final_multiplier),
            'tier_matched': best_match.get('description', 'Unknown'),
            'bonuses_applied': final_multiplier > best_match['capital_multiplier']
        }

        self._print_decision(probability, sqs, result, rr_ratio)
        return result

    def _find_best_tier_match(self, probability: float, sqs: float) -> Dict:
        """Find the best matching tier with highest capital multiplier"""
        matching_tiers = []

        for tier in self._config["probability_tiers"]:
            if (probability >= tier["min_probability"] and
                sqs >= tier["min_sqs"]):
                matching_tiers.append(tier)

        if not matching_tiers:
            # Fallback to minimum tier
            return {
                "capital_multiplier": 1.0,
                "description": "Minimum requirements met"
            }

        # Return tier with highest capital multiplier
        return max(matching_tiers, key=lambda x: x["capital_multiplier"])

    def _apply_bonuses(self, probability: float, sqs: float, base_multiplier: float) -> float:
        """Apply special bonuses and caps"""
        multiplier = base_multiplier

        # Institutional grade bonus
        if "special_bonuses" in self._config:
            institutional = self._config["special_bonuses"].get("institutional_grade_bonus", {})
            if sqs >= institutional.get("sqs_threshold", 85):
                bonus = institutional.get("bonus_multiplier", 0.5)
                multiplier += bonus
                logger.info(f"🏛️ INSTITUTIONAL BONUS: +{bonus}x for SQS {sqs}")

            # Double excellence check
            double_exc = self._config["special_bonuses"].get("double_excellence", {})
            if (probability >= double_exc.get("min_probability", 62) and
                sqs >= double_exc.get("min_sqs", 75)):
                multiplier = max(multiplier, double_exc.get("capital_multiplier", 3.0))
                logger.info(f"💎 DOUBLE EXCELLENCE: {probability}%/{sqs} SQS")

        # Apply cap
        max_mult = self._config.get("risk_management", {}).get("max_capital_multiplier", 3.0)
        return min(multiplier, max_mult)

    def _classify_quality(self, probability: float, sqs: float) -> str:
        """Classify overall trade quality"""
        if probability >= 63 and sqs >= 75:
            return "EXCEPTIONAL"
        elif probability >= 60 and sqs >= 65:
            return "PREMIUM"
        elif probability >= 60 or sqs >= 65:
            return "GOOD"
        elif probability >= 57 and sqs >= 50:
            return "FAIR"
        else:
            return "ACCEPTABLE"

    def _classify_frequency(self, probability: float, sqs: float) -> str:
        """Classify how often we expect to see this combination"""
        if probability >= 63:
            return "RARE" if sqs >= 60 else "VERY_RARE"
        elif probability >= 60:
            return "UNCOMMON" if sqs >= 50 else "COMMON"
        elif probability >= 57:
            return "COMMON" if sqs < 70 else "UNCOMMON"
        else:
            return "FREQUENT" if sqs < 60 else "COMMON"

    def _create_breakdown(self, probability: float, sqs: float, multiplier: float = 0.0, rejected: bool = False) -> Dict:
        """Create detailed breakdown for logging"""
        return {
            "probability": {
                "value": probability,
                "tier": "RARE" if probability >= 63 else "GOOD" if probability >= 60 else "STANDARD"
            },
            "sqs": {
                "value": sqs,
                "grade": self._interpret_sqs_grade(sqs)
            },
            "decision": {
                "multiplier": multiplier,
                "rejected": rejected
            }
        }

    def _interpret_sqs_grade(self, sqs: float) -> str:
        """Interpret SQS using same scale as crypto-analyzer-redis"""
        if sqs >= 85:
            return "INSTITUTIONAL"
        elif sqs >= 75:
            return "HIGH_QUALITY"
        elif sqs >= 65:
            return "GOOD_QUALITY"
        elif sqs >= 55:
            return "FAIR_QUALITY"
        elif sqs >= 45:
            return "BELOW_AVERAGE"
        else:
            return "POOR_QUALITY"

    def _default_decision(self, probability: float, sqs: float) -> Dict:
        """Default decision when config is disabled"""
        return {
            'action': 'accept',
            'capital_multiplier': 1.0,
            'quality_grade': 'DEFAULT',
            'frequency_class': 'UNKNOWN',
            'reason': 'SQS evaluation disabled - default accept',
            'breakdown': self._create_breakdown(probability, sqs, 1.0)
        }

    def _print_decision(self, probability: float, sqs: float, decision: Dict, rr_ratio: float = 1.0):
        """Print detailed decision breakdown with colors and emojis"""
        symbol = self._current_symbol or 'TRADE'

        # Header
        print(f"\n{'='*80}")
        print(f"🎯 SQS TRADE EVALUATION: {symbol}")
        print(f"{'='*80}")

        # Input data
        sqs_grade = self._interpret_sqs_grade(sqs)
        prob_tier = "RARE" if probability >= 63 else "GOOD" if probability >= 60 else "STANDARD"

        print(f"📊 INPUT DATA:")
        print(f"   📈 Probability: {probability:.1f}% ({prob_tier})")
        print(f"   🔍 SQS: {sqs:.1f}/100 ({sqs_grade})")
        print(f"   ⚖️  RR Ratio: {rr_ratio:.2f}")

        # Decision
        action = decision['action'].upper()
        multiplier = decision['capital_multiplier']

        if action == 'REJECT':
            print(f"\n❌ DECISION: {action}")
            print(f"   🚫 Capital: 0.0x (TRADE BLOCKED)")
            print(f"   📝 Reason: {decision['reason']}")
        else:
            # Determine scenario type based on prob/sqs combination
            scenario = self._determine_scenario_type(probability, sqs, multiplier)

            print(f"\n✅ DECISION: {action}")
            print(f"   💰 Capital Multiplier: {multiplier:.1f}x")
            print(f"   🏆 Quality Grade: {decision['quality_grade']}")
            print(f"   📊 Frequency Class: {decision['frequency_class']}")
            print(f"   🎲 Scenario Type: {scenario}")
            print(f"   📝 Rule Matched: {decision.get('tier_matched', 'Default')}")

            # Show bonuses if any
            if decision.get('bonuses_applied', False):
                print(f"   🎁 Special Bonuses: APPLIED")

        # Footer with summary
        print(f"{'='*80}")
        if action == 'ACCEPT':
            capital_emoji = "🚀" if multiplier >= 2.0 else "📈" if multiplier >= 1.5 else "💰"
            print(f"{capital_emoji} SUMMARY: {prob_tier} probability + {sqs_grade} SQS → {multiplier:.1f}x capital")
        else:
            print(f"🛑 SUMMARY: Trade rejected - insufficient quality")
        print(f"{'='*80}\n")

    def _determine_scenario_type(self, probability: float, sqs: float, multiplier: float) -> str:
        """Determine the scenario type based on prob/sqs combination"""

        # Exceptional scenarios
        if probability >= 63 and sqs >= 75:
            return "🌟 JACKPOT (Rare Prob + Excellent SQS)"
        elif probability >= 63:
            return "🎯 HIGH_PROBABILITY_CARRY (Rare prob compensates low SQS)"
        elif sqs >= 85:
            return "🏛️ INSTITUTIONAL_GRADE (Excellent SQS compensates standard prob)"
        elif probability >= 60 and sqs >= 70:
            return "💎 PREMIUM_QUALITY (Good prob + high SQS)"

        # Good scenarios
        elif probability >= 60 and sqs >= 50:
            return "📈 BALANCED_GOOD (Good prob + fair SQS)"
        elif probability >= 60:
            return "🎲 PROBABILITY_DRIVEN (Good prob compensates below-avg SQS)"
        elif sqs >= 70:
            return "🔍 QUALITY_DRIVEN (High SQS compensates standard prob)"

        # Standard scenarios
        elif probability >= 57 and sqs >= 55:
            return "⚖️ STANDARD_BALANCED (Moderate prob + fair SQS)"
        elif sqs >= 60:
            return "📊 SQS_ADVANTAGE (Good SQS with moderate prob)"
        else:
            return "🎯 MINIMUM_VIABLE (Meets minimum requirements)"